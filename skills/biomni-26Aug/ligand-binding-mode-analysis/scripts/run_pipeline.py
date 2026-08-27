"""
End-to-end orchestrator for binding-pocket contact mapping.

Ties together: fetch -> ligand select -> fragments -> contacts -> interaction
typing -> (optional kinase layer) -> (optional cross-structure concordance) ->
figures -> 3D -> CSV -> report payload.

Typical use (from a Biomni agent session):

    from scripts.run_pipeline import run_analysis
    payload = run_analysis(
        primary="1IEP",                 # PDB id, local file path, or {"target":..,"ligand":..}
        ligand_code="STI",              # optional; auto-detected if omitted
        comparisons=["2HYY"],           # optional list of PDB ids / paths
        extended_interactions=False,    # core 3 by default (prompt the user!)
        out_dir="pocket_analysis",
    )
    # then build the PDF (references come from the agent's LiteratureSearch call):
    from scripts.build_report import build_report, validate_pdf
    build_report(payload, "/mnt/results/report_pocket.pdf")

Literature: this module does NOT call the platform LiteratureSearch tool itself
(that is an agent tool). Pass real results via `references=` to run_analysis or set
payload["references"] before build_report. Never fabricate references.
"""

import csv
import os
from collections import Counter

import fetch_structure as fs
import find_ligands as fl
import ligand_fragments as lfrag
import compute_contacts as cc
import classify_interactions as ci
import plip_backend as plip
import kinase_annotate as ka
import compare_structures as cmp_s
import make_figures as mf
import render_3d as r3d
import literature_context as lit


def _relevant_chains(contacts, cut=4.5, frac=0.25, min_extra=3):
    """Chains that genuinely line the pocket for the *selected ligand copy*.

    A protein may appear in several chains for two very different reasons:

      (a) Crystallographic copies of a single-chain site (e.g. two protomers in
          the asymmetric unit, each with its own bound ligand). Here the ligand
          copy we selected touches essentially ONE chain; the others belong to
          the *other* ligand copies and must be dropped to avoid double-counting.

      (b) A composite site built from several chains at once (e.g. the HIV-1
          protease homodimer, where one inhibitor sits in the dimer interface
          and contacts BOTH monomers, including the catalytic Asp25/Asp25').
          Here every contacting chain is part of the real pocket and must be kept.

    We decide from the selected ligand copy's own contacts: keep the dominant
    chain, plus any other chain contributing at least `frac` of the dominant
    chain's close-contact count and at least `min_extra` close contacts. This
    keeps single-chain pockets single (non-dominant chains contribute ~0) while
    retaining all genuine partners of an interface pocket.
    """
    close = [c for c in contacts if c["min_dist"] <= cut]
    if not close:
        return None  # signal: no filtering
    counts = Counter(c["chain"] for c in close)
    dom_chain, dom_n = counts.most_common(1)[0]
    keep = {dom_chain}
    for ch, n in counts.items():
        if ch == dom_chain:
            continue
        if n >= max(min_extra, frac * dom_n):
            keep.add(ch)
    return keep


def _resolve_input(spec, out_dir):
    """Turn a spec (PDB id | path | {target,ligand}) into a structure dict."""
    struct_dir = os.path.join(out_dir, "structures")
    if isinstance(spec, dict):
        target = spec.get("target")
        ligand = spec.get("ligand")
        hits = fs.rank_cocrystals(target, ligand_code=ligand)
        if not hits:
            raise ValueError(f"No RCSB co-crystals found for target={target!r} ligand={ligand!r}")
        best = hits[0]["pdb_id"]
        print(f"[info] selected {best} from {len(hits)} candidates for {target!r}")
        return fs.fetch_pdb(best, out_dir=struct_dir)
    if os.path.exists(spec):
        return fs.load_local_structure(spec, out_dir=struct_dir)
    return fs.fetch_pdb(spec, out_dir=struct_dir)


def _nearest_fragment_for_contacts(contacts, fragment_map):
    for c in contacts:
        c["nearest_fragment"] = fragment_map.get(c.get("nearest_lig_atom", ""), "scaffold")
    return contacts


def _write_csv(contacts, path, has_kinase, comparison_rows=None, comparison_labels=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmp_by = {}
    if comparison_rows:
        cmp_by = {(r["resname"], r["resseq"]): r for r in comparison_rows}
    fields = ["residue", "resname", "resseq", "chain", "min_dist", "core_contact",
              "n_contacts_4p0", "n_contacts_4p5", "nearest_lig_atom",
              "nearest_fragment", "interaction_type", "interaction_confidence",
              "interaction_source", "n_hbonds", "hbond_details"]
    if has_kinase:
        fields.insert(4, "kinase_region")
    if comparison_labels:
        for l in comparison_labels:
            fields.append(f"min_dist_{l}")
        fields.append("conserved_identity")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for c in sorted(contacts, key=lambda x: x["min_dist"]):
            row = {
                "residue": f"{c['resname']}{c['resseq']}",
                "resname": c["resname"], "resseq": c["resseq"], "chain": c.get("chain", ""),
                "min_dist": c["min_dist"], "core_contact": c["core_contact"],
                "n_contacts_4p0": c["n_core"], "n_contacts_4p5": c["n_wide"],
                "nearest_lig_atom": c.get("nearest_lig_atom", ""),
                "nearest_fragment": c.get("nearest_fragment", ""),
                "interaction_type": c.get("interaction_type", "vdW"),
                "interaction_confidence": c.get("interaction_confidence", "high"),
                "interaction_source": c.get("interaction_source", "geometry"),
                "n_hbonds": len(c["hbonds"]),
                "hbond_details": "; ".join(f"{h['prot_atom']}-{h['lig_atom']}({h['dist']})"
                                           for h in c["hbonds"]),
            }
            if has_kinase:
                row["kinase_region"] = c.get("kinase_region", "")
            if comparison_labels:
                m = cmp_by.get((c["resname"], c["resseq"]), {})
                for l in comparison_labels:
                    row[f"min_dist_{l}"] = m.get(f"min_dist_{l}", "")
                row["conserved_identity"] = m.get("identity_conserved", "")
            w.writerow(row)
    print(f"[OK] wrote {path}")
    return path


def run_analysis(primary, ligand_code=None, comparisons=None, extended_interactions=False,
                 out_dir="pocket_analysis", target_name=None, make_3d=True,
                 references=None, primary_chain=None, use_plip=True):
    """
    Run the full analysis and return a `payload` dict for build_report.

    See module docstring for parameters. `references` should be a list of real
    reference dicts (e.g. from the agent's LiteratureSearch); never fabricated.
    """
    comparisons = comparisons or []
    fig_dir = os.path.join(out_dir, "figures")
    os.makedirs(out_dir, exist_ok=True)

    # ---- primary structure ----
    prim = _resolve_input(primary, out_dir)
    structure = fl.load_structure(prim["path"], structure_id=prim["pdb_id"])
    meta = fs.get_entry_metadata(prim["pdb_id"]) if len(prim["pdb_id"]) == 4 else {"pdb_id": prim["pdb_id"]}

    lig = fl.select_target_ligand(structure, prefer=ligand_code)
    lig_code = lig["resname"]
    lig_meta = fs.get_ligand_metadata(lig_code) if len(lig_code) <= 3 else {"code": lig_code}
    lig_res, lig_chain = fl.get_ligand_residue(structure, lig_code, chain_id=None)
    analysis_chain = primary_chain or lig_res.get_parent().id

    # ---- fragments ----
    # Pass the RCSB canonical SMILES so RDKit can transfer bond orders and
    # correctly perceive aromatic rings (see ligand_fragments docstring).
    fragment_map, frag_method = lfrag.assign_fragments(
        lig_res, smiles=lig_meta.get("smiles"))

    # ---- contacts on the chain that holds the ligand ----
    # use chains that actually contact this ligand copy: default = all, then we let
    # min-dist filtering keep the relevant protomer. To avoid mixing protomers, we
    # restrict to residues in the same chain as the ligand plus any within range.
    contacts = cc.compute_contacts(structure, lig_res)
    # Keep the chain(s) that genuinely line this ligand copy's pocket. For a
    # single-chain site this is just the ligand's own chain; for a composite
    # interface site (e.g. HIV protease dimer) it keeps every contacting chain.
    keep_chains = _relevant_chains(contacts)
    if keep_chains is not None:
        contacts = [c for c in contacts if c["chain"] in keep_chains]
    analysis_chains = keep_chains or {analysis_chain}
    contacts = _nearest_fragment_for_contacts(contacts, fragment_map)

    # ---- interaction typing ----
    # Primary engine: PLIP (peer-reviewed; enforces donor/acceptor angles). The
    # hardened built-in geometry is always computed too, both as the automatic
    # fallback and to type any residue PLIP did not report. Every contact carries a
    # `confidence` tier ("high"/"tentative") and a `source` ("PLIP"/"geometry").
    contacts, extra = ci.classify(structure, lig_res, contacts, fragment_map,
                                  extended=extended_interactions,
                                  smiles=lig_meta.get("smiles"))
    typing_engine = "geometry"
    plip_info = {"used": False, "version": None, "records": None}
    if use_plip:
        try:
            plip_out = plip.profile_with_plip(
                prim["path"], lig_code, chain=lig_res.get_parent().id,
                position=lig_res.id[1])
        except Exception as e:  # noqa: BLE001
            print(f"[warn] PLIP call raised ({e}); using hardened geometry only")
            plip_out = None
        if plip_out is not None:
            contacts = ci.merge_plip_tags(contacts, plip_out["by_residue"])
            typing_engine = "PLIP"
            plip_info = {"used": True, "version": plip_out.get("version"),
                         "records": {k: len(v) for k, v in plip_out["records"].items()},
                         "water_bridges": plip_out["records"].get("water_bridge", [])}
            print(f"[OK] PLIP typing applied (v{plip_out.get('version')}): "
                  + ", ".join(f"{k}={n}" for k, n in plip_info["records"].items()))
        else:
            print("[info] PLIP unavailable/failed -> hardened geometry typing (with angle/charge checks)")
    type_counts = ci.interaction_type_counts(contacts)
    conf_counts = ci.confidence_counts(contacts)

    # ---- optional kinase layer ----
    kinfo = ka.detect_kinase(structure, contact_chain_id=analysis_chain)
    contacts = ka.annotate_contacts(contacts, kinfo)
    kinase_payload = {"is_kinase": kinfo.get("is_kinase", False)}
    if kinfo.get("is_kinase"):
        kinase_payload["summary_line"] = ka.kinase_summary(kinfo)

    summ = cc.summarize_contacts(contacts)

    # ---- key residues: H-bonders + closest, up to 6 ----
    key = [c for c in contacts if c["hbonds"]]
    key_sorted = sorted(key, key=lambda c: c["min_dist"])
    for c in sorted(contacts, key=lambda c: c["min_dist"]):
        if len(key_sorted) >= 6:
            break
        if c not in key_sorted:
            key_sorted.append(c)
    # carry (resseq, chain) so multi-chain pockets (dimer interface sites) render
    # and label the correct copy; single-chain pockets are unaffected.
    key_residues = [(c["resseq"], c.get("chain", "")) for c in key_sorted[:6]]
    summ["n_key_residues"] = len(key_residues)

    # ---- comparisons / concordance ----
    comparison_payload = None
    comp_rows, comp_labels = None, None
    if comparisons:
        others, labels = [], []
        for cspec in comparisons:
            try:
                cd = _resolve_input(cspec, out_dir)
                cs_struct = fl.load_structure(cd["path"], structure_id=cd["pdb_id"])
                clig = fl.select_target_ligand(cs_struct, prefer=lig_code)
                clig_res, _ = fl.get_ligand_residue(cs_struct, clig["resname"])
                ccontacts = cc.compute_contacts(cs_struct, clig_res)
                ckeep = _relevant_chains(ccontacts)
                if ckeep is not None:
                    ccontacts = [x for x in ccontacts if x["chain"] in ckeep]
                others.append(ccontacts)
                labels.append(cd["pdb_id"])
            except Exception as e:  # noqa: BLE001
                print(f"[warn] comparison {cspec} skipped: {e}")
        if others:
            comp_rows = cmp_s.concordance_by_number(contacts, others, labels)
            comp_labels = labels
            csum = cmp_s.summarize_concordance(comp_rows, 1 + len(others))
            comparison_payload = {
                "rows": comp_rows, "labels": labels, "summary": csum,
                "paragraphs": [
                    f"The primary pocket was compared against {len(others)} additional "
                    f"structure(s) ({', '.join(labels)}). Of {csum['n_pocket_residues']} "
                    f"primary pocket residues, {csum['n_present_in_all']} "
                    f"({csum['frac_present_in_all']*100:.0f}%) are reproduced in every "
                    f"structure and {csum['n_identity_conserved']} retain the same amino-acid "
                    f"identity. Median variation in minimum contact distance across structures "
                    f"is {csum['median_distance_spread_A']} &#197;, indicating a well-conserved "
                    f"binding mode." if csum["median_distance_spread_A"] is not None else
                    f"Compared against {', '.join(labels)}."],
            }

    # ---- figures ----
    figures = {}
    lig_label = lig_code
    try:
        figures["interaction"] = mf.figure_interaction_diagram(
            contacts, lig_label, os.path.join(fig_dir, "F1_interaction_diagram"))[0]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] F1 failed: {e}")
    try:
        figures["distance"] = mf.figure_contact_distance(
            contacts, os.path.join(fig_dir, "F2_contact_distance"))[0]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] F2 failed: {e}")
    try:
        figures["heatmap"] = mf.figure_fragment_heatmap(
            structure, lig_res, contacts, fragment_map,
            os.path.join(fig_dir, "F3_fragment_heatmap"), cc.contacts_by_ligand_atom)[0]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] F3 failed: {e}")

    # ---- 3D ----
    render_engine = None  # "pymol" | "matplotlib" | None (no 3D / failed)
    if make_3d:
        hbonds_for_3d = []
        for c in contacts:
            for h in c["hbonds"]:
                hbonds_for_3d.append({"prot_resseq": c["resseq"], "prot_chain": c.get("chain", ""),
                                      "prot_atom": h["prot_atom"], "lig_atom": h["lig_atom"]})
        try:
            out3d = r3d.render_pocket_3d(
                structure, prim["path"], lig_res, lig_code, key_residues, hbonds_for_3d,
                os.path.join(fig_dir, "F4_pocket_3D.png"),
                os.path.join(fig_dir, "F4b_hbond_closeup.png"),
                ligand_chain=lig_chain)
            figures["pocket3d"] = out3d.get("overview")
            figures["closeup3d"] = out3d.get("closeup")
            render_engine = out3d.get("engine")  # set by render_3d.render_pocket_3d
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 3D render failed: {e}")

    # ---- CSV ----
    has_kinase = kinase_payload["is_kinase"]
    csv_path = os.path.join(out_dir, "pocket_contacts.csv")
    _write_csv(contacts, csv_path, has_kinase, comp_rows, comp_labels)

    # ---- assemble payload ----
    tname = target_name or (meta.get("title") or prim["pdb_id"])
    structures_line = prim["pdb_id"] + (f" (+ {', '.join(comp_labels)})" if comp_labels else "")
    interaction_mode = "extended (H-bond, salt bridge, hydrophobic, pi-stacking, pi-cation, halogen)" \
        if extended_interactions else "core (H-bond, salt bridge, hydrophobic/vdW)"

    payload = {
        "target": {"name": tname, "pdb_id": prim["pdb_id"], "method": meta.get("method"),
                   "resolution_A": meta.get("resolution_A")},
        "ligand": {"code": lig_code, "name": lig_meta.get("name"),
                   "formula": lig_meta.get("formula"),
                   "formula_weight": lig_meta.get("formula_weight"),
                   "smiles": lig_meta.get("smiles")},
        "structures_line": structures_line,
        "summary": summ,
        "type_counts": type_counts,
        "confidence_counts": conf_counts,
        "typing_engine": typing_engine,
        "plip_info": plip_info,
        "charge_method": extra.get("charge_method"),
        "key_residues": key_residues,
        "contacts": contacts,
        "kinase": kinase_payload,
        "comparison": comparison_payload,
        "figures": figures,
        "references": lit.format_references(references),
        "fragment_method": frag_method,
        "extended": extended_interactions,
        "csv_path": csv_path,
        "intro_paragraphs": _intro(tname, lig_code, lig_meta, prim, summ, kinase_payload),
        "methods_paragraphs": _methods(prim, meta, interaction_mode, frag_method, comp_labels,
                                       extended_interactions, render_engine, typing_engine,
                                       plip_info, extra.get("charge_method")),
        "results_paragraphs": _results(summ, contacts, type_counts, kinase_payload, lig_code, conf_counts),
        "conclusions_paragraphs": _conclusions(summ, kinase_payload, comparison_payload, lig_code, tname),
        "caveats": _caveats(extended_interactions, typing_engine, plip_info),
        "next_steps": _next_steps(kinase_payload),
    }
    return payload


# ---- narrative builders (data-driven, no fabricated numbers) ----

def _intro(tname, lig_code, lig_meta, prim, summ, kinase):
    name = lig_meta.get("name") or lig_code
    k = " This target is a protein kinase; pocket residues are additionally annotated by their catalytic role." if kinase["is_kinase"] else ""
    return [
        f"This report maps the binding-pocket contacts between the ligand {lig_code}"
        f"{(' (' + name + ')') if name and name != lig_code else ''} and its protein target "
        f"in structure {prim['pdb_id']}. The analysis identifies which residues line the "
        f"binding site, how close they approach the ligand, and what type of interaction each "
        f"contact represents.{k}",
        f"In total, {summ['n_contact_residues']} residues lie within 4.5 &#197; of the ligand, "
        f"{summ['n_core_residues']} of them within the 4.0 &#197; core packing shell, and "
        f"{summ['n_hbonds']} candidate hydrogen bonds anchor the ligand in the pocket. These "
        f"contacts define the structural basis of recognition and provide a starting point for "
        f"medicinal-chemistry optimization.",
    ]


def _methods(prim, meta, interaction_mode, frag_method, comp_labels, extended, render_engine=None,
             typing_engine="geometry", plip_info=None, charge_method=None):
    plip_info = plip_info or {"used": False}
    res = meta.get("resolution_A")
    resline = f" solved at {res} &#197; resolution" if res else ""
    comp = f" The pocket was compared against {', '.join(comp_labels)} to assess reproducibility." if comp_labels else ""
    # The 3D narrative must reflect the engine actually used. render_3d.render_pocket_3d
    # returns {"engine": "pymol"|"matplotlib"}; when 3D is disabled or fails entirely,
    # render_engine is None and we describe the view as unavailable.
    if render_engine == "pymol":
        render_line = "the 3D pocket view was rendered with PyMOL"
    elif render_engine == "matplotlib":
        render_line = ("the 3D pocket view was rendered with a matplotlib 3D scatter fallback "
                       "(PyMOL was unavailable)")
    else:
        render_line = "the 3D pocket view was not produced"
    return [
        f"Coordinates for {prim['pdb_id']}{resline} were obtained from the RCSB PDB. The target "
        f"ligand was identified automatically after excluding crystallographic additives (waters, "
        f"ions, buffers, cryoprotectants).{comp}",
        f"Protein&#8211;ligand contacts were computed by heavy-atom distance geometry (Biopython). "
        f"A residue is a contact if any heavy atom lies within 4.5 &#197; of the ligand; the 4.0 &#197; "
        f"shell defines the core packing contacts. Candidate hydrogen bonds were assigned to "
        f"polar (N/O) donor&#8211;acceptor pairs within 3.5 &#197;. Because crystallographic models "
        f"lack hydrogen atoms, hydrogen bonds and other polar interactions are geometric "
        f"(distance-based) assignments rather than energy calculations.",
        f"Interaction typing was performed with {_engine_sentence(typing_engine, plip_info, charge_method)} "
        f"using the {interaction_mode} interaction set. Each interaction call is labelled with a "
        f"confidence tier: <b>high</b> when it meets strict geometry (including donor/acceptor "
        f"angles for hydrogen and halogen bonds, and genuine formal charges for salt bridges and "
        f"pi-cation interactions) or is confirmed by PLIP, and <b>tentative</b> when it is "
        f"distance-only, near a threshold, or dependent on an assumed protonation state. "
        f"Ligand atoms were grouped into chemical fragments using "
        f"{'RDKit bond perception (bond orders transferred from the RCSB canonical SMILES)' if frag_method=='rdkit' else 'a coordinate-based bonding graph'} "
        f"so that contacts can be summarized by chemically meaningful pieces of the molecule. "
        f"Figures and a machine-readable contact table were generated; {render_line}.",
    ]


def _engine_sentence(typing_engine, plip_info, charge_method):
    if typing_engine == "PLIP" and plip_info.get("used"):
        return (f"the Protein&#8211;Ligand Interaction Profiler (PLIP v{plip_info.get('version','?')}), a "
                f"peer-reviewed tool that protonates the complex and applies published geometric "
                f"criteria (with donor/acceptor angles) as the primary engine, backed by a hardened "
                f"in-house distance/angle geometry engine for any residue PLIP did not type")
    charge_note = ("formal charges transferred from the ligand SMILES" if charge_method == "rdkit_formal"
                   else "a coarse element-based charge proxy (formal charges unavailable)")
    return (f"a hardened distance/angle geometry engine (Biopython + RDKit) that enforces "
            f"donor/acceptor angles for hydrogen and halogen bonds and derives ionic centres from "
            f"{charge_note}")


def _results(summ, contacts, type_counts, kinase, lig_code, conf_counts=None):
    top = sorted(contacts, key=lambda c: c["min_dist"])[:5]
    top_str = ", ".join(f"{c['resname']}{c['resseq']} ({c['min_dist']:.2f} &#197;)" for c in top)
    tc = ", ".join(f"{v} {k}" for k, v in sorted(type_counts.items(), key=lambda x: -x[1]))
    hb_res = [c for c in contacts if c["hbonds"]]
    hb_str = "; ".join(
        f"{c['resname']}{c['resseq']} (" + ", ".join(f"{h['prot_atom']}&#8211;{h['lig_atom']} {h['dist']} &#197;"
                                                      for h in c["hbonds"][:2]) + ")"
        for c in sorted(hb_res, key=lambda c: c["min_dist"])[:6])
    conf_str = ""
    if conf_counts:
        conf_str = (f" Of the typed contacts, {conf_counts.get('high', 0)} are high-confidence and "
                    f"{conf_counts.get('tentative', 0)} are tentative (flagged in the table).")
    paras = [
        f"The closest contacts to {lig_code} are {top_str}. Across the pocket, contacts break down "
        f"by type (per-residue tallies) as: {tc} (residues may contribute more than one interaction "
        f"type).{conf_str}",
    ]
    # highlight any tentative calls explicitly for honesty
    tentative = [c for c in contacts if c.get("interaction_confidence") == "tentative"
                 and c.get("interaction_type") not in (None, "vdW", "hydrophobic")]
    if tentative:
        t_str = "; ".join(f"{c['resname']}{c['resseq']} ({c['interaction_type']})"
                          for c in sorted(tentative, key=lambda c: c["min_dist"]))
        paras.append(f"The following contacts are reported as <b>tentative</b> and should be confirmed "
                     f"structurally before over-interpretation: {t_str}.")
    if hb_str:
        paras.append(f"Candidate hydrogen bonds are formed by {hb_str}. These polar anchors typically "
                     f"dominate the specificity of binding.")
    if kinase.get("is_kinase"):
        paras.append(kinase.get("summary_line", "") +
                     " Contacts mapped onto these motifs indicate how the ligand engages the "
                     "kinase catalytic machinery (e.g. hinge hydrogen bonds and DFG-motif contacts).")
    return paras


def _conclusions(summ, kinase, comparison, lig_code, tname):
    paras = [
        f"{lig_code} occupies a well-defined pocket in {tname}, engaging "
        f"{summ['n_contact_residues']} residues through a combination of hydrogen bonding and "
        f"shape-complementary van der Waals contacts. The {summ['n_hbonds']} candidate hydrogen "
        f"bonds provide directional anchoring while the larger apolar surface provides affinity.",
    ]
    if comparison and comparison.get("summary", {}).get("frac_present_in_all") is not None:
        f = comparison["summary"]["frac_present_in_all"]
        paras.append(f"The pocket is reproducible: {f*100:.0f}% of contacting residues recur across "
                     f"the compared structures with conserved identity, arguing that the mapped "
                     f"contacts reflect a genuine binding mode rather than crystal-packing artifacts.")
    return paras


def _caveats(extended, typing_engine="geometry", plip_info=None):
    plip_info = plip_info or {"used": False}
    c = [
        "Contacts and hydrogen bonds are geometric assignments from a single static crystal "
        "structure; they do not capture conformational dynamics, solvent screening, or binding "
        "free energy.",
        "Hydrogen bonds are reported as candidates: crystallographic models generally lack hydrogen "
        "atoms, so although a donor/acceptor angle is applied when the engine can place polar "
        "hydrogens (PLIP) or infer geometry, the precise proton positions are not observed.",
        "Ordered waters were excluded from the quantitative contact set; bridging-water contacts "
        "are not counted here (PLIP-detected water bridges, if any, are listed separately).",
        "Interaction calls are tiered by confidence. <b>Tentative</b> calls (distance-only, "
        "near-threshold, or dependent on an assumed ligand protonation/charge state) should be "
        "confirmed in the 3D structure before they are used to support a conclusion.",
    ]
    if plip_info.get("used"):
        c.append("Salt-bridge and pi-cation calls depend on the protonation/charge state PLIP assigns "
                 "to the ligand; weakly basic groups (e.g. a morpholine nitrogen, pKa ~5&#8211;6) may be "
                 "largely neutral at physiological pH, so such ionic interactions are treated cautiously.")
    else:
        c.append("Interaction typing used the in-house geometry engine (PLIP was not available); "
                 "ionic interactions rely on RDKit formal charges where derivable and are otherwise "
                 "downgraded to tentative.")
    if extended:
        c.append("Extended interaction types (pi-stacking, pi-cation, halogen bonds) remain the most "
                 "sensitive to geometry; even high-confidence calls benefit from visual confirmation.")
    return c


def _next_steps(kinase):
    steps = [
        "Validate key hydrogen bonds and any salt bridges by inspecting the 3D structure in a "
        "molecular viewer.",
        "Prioritize pocket residues for mutagenesis to test their contribution to binding.",
        "Use the fragment&#8211;residue map to guide analog design (which chemical group to modify "
        "to reach or avoid a given residue).",
        "Predict ADMET properties of the ligand (Biomni predict_admet_properties) and dock analogs "
        "against the pocket (AutoDock Vina).",
    ]
    if kinase.get("is_kinase"):
        steps.append("Assess the DFG-in/DFG-out conformation and hinge-binding pattern to classify the "
                     "inhibitor type (type I vs type II) and inform selectivity design.")
    steps.append("Cross-reference additional co-crystal structures or run a short molecular-dynamics "
                 "simulation to confirm the stability of the mapped contacts.")
    return steps


if __name__ == "__main__":
    import sys

    pid = sys.argv[1] if len(sys.argv) > 1 else "1IEP"
    pl = run_analysis(pid, out_dir="pocket_analysis_cli")
    from build_report import build_report, validate_pdf
    out = build_report(pl, f"/mnt/results/report_pocket_{pid}.pdf")
    validate_pdf(out)

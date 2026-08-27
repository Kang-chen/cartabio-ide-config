"""
Render a 3D view of the binding pocket.

Primary path: headless PyMOL (pymol-open-source). Draws the protein cartoon
(transparent), the ligand as sticks, the top contacting residues as sticks, and
candidate H-bonds as dashed distance objects. Produces a pocket overview and an
H-bond close-up.

Fallback path: if PyMOL is unavailable, a matplotlib 3D scatter of ligand + pocket
Calpha atoms so the pipeline never hard-fails (lower quality, clearly labelled).

PyMOL install (if missing):
    conda install -n base -c conda-forge pymol-open-source -y

Key render settings mirror the validated imatinib-ABL1 renders: cartoon
transparency 0.72, ligand stick radius 0.23, H-bond distances in red, ray_shadows
off, antialias 2, generous zoom padding so labels are not clipped.
"""

import os


def _have_pymol():
    try:
        import pymol  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def render_pymol(structure_path, ligand_resname, key_residues, hbonds,
                 out_overview, out_closeup=None, ligand_chain=None,
                 ligand_resi=None):
    """
    Render pocket with PyMOL.

    Parameters
    ----------
    structure_path : str   path to the .pdb/.cif used for analysis
    ligand_resname : str   ligand chem-comp code (e.g. 'STI')
    ligand_resi : int      residue number of the *specific* ligand copy to render.
                           Structures often contain several copies of the same
                           ligand code (e.g. 3 acetazolamide molecules in one CA-II
                           crystal); scoping by resi (in addition to chain) ensures
                           only the analysed copy is drawn instead of all of them.
    key_residues : list        residue specs to show as sticks + label. Each item
                               is either a bare int resseq (single-chain pocket)
                               or a (resseq, chain) tuple. Tuples let a pocket that
                               spans several chains (e.g. a homodimer interface
                               site) label the correct copy in each chain.
    hbonds : list[dict]    each {prot_resseq, prot_atom, lig_atom[, prot_chain]}
    out_overview : str     output PNG for the pocket overview
    out_closeup : str      optional output PNG for an H-bond close-up
    ligand_chain : str     chain of the ligand copy to render. For a single-chain
                           pocket this also scopes the pocket; for a multi-chain
                           pocket the per-residue chain in `key_residues`/`hbonds`
                           is used so genuine cross-chain contacts are kept.

    Selections are chain-scoped per residue so `resi N` never matches the same
    number in an unrelated chain (which would duplicate sticks/labels or draw
    H-bond dashes to symmetry-related copies).
    """
    import pymol
    from pymol import cmd

    def _norm(item):
        """Return (resseq, chain_or_None) from an int or (resseq, chain) tuple."""
        if isinstance(item, (tuple, list)):
            r = item[0]
            c = item[1] if len(item) > 1 and item[1] else None
            return r, c
        return item, None

    def _res_sel(r, c, base="polymer"):
        """A residue selection scoped to its chain (falls back to ligand_chain)."""
        chain = c or ligand_chain
        s = f"{base} and resi {r}"
        if chain:
            s += f" and chain {chain}"
        return s

    key_norm = [_norm(k) for k in key_residues]
    # chains that the pocket actually spans (from key residues + ligand chain)
    pocket_chains = {c for _, c in key_norm if c}
    if ligand_chain:
        pocket_chains.add(ligand_chain)
    # when the pocket spans >1 protein chain (e.g. a homodimer interface pocket),
    # residue numbers alone are ambiguous (ASP25 exists in both protomers), so we
    # append the chain to labels and colour the protomers distinctly.
    prot_chains_only = sorted(c for c in pocket_chains if c and c != ligand_chain) or \
        sorted(c for c in pocket_chains if c)
    multichain = len({c for _, c in key_norm if c}) > 1

    pymol.finish_launching(["pymol", "-qc"])
    cmd.reinitialize()
    cmd.load(structure_path, "s")
    cmd.hide("everything")

    # Cartoon: show every chain that lines the pocket (one protomer for a simple
    # site, both monomers for an interface site) so the context is honest but not
    # cluttered with unrelated copies.
    if pocket_chains:
        poly = "(polymer and (" + " or ".join(f"chain {c}" for c in sorted(pocket_chains)) + "))"
    else:
        poly = "polymer"

    lig_sel = f"resn {ligand_resname}"
    if ligand_chain:
        lig_sel += f" and chain {ligand_chain}"
    if ligand_resi is not None:
        # scope to the single analysed copy so multi-copy crystals (e.g. 3x AZM
        # in CA-II) do not draw every ligand molecule in the asymmetric unit.
        lig_sel += f" and resi {ligand_resi}"
    cmd.select("lig", lig_sel)
    # keep only one ligand copy as an isolated object
    cmd.create("ligobj", "lig")
    # if multiple copies still collapsed in, keep the first segment/state only
    cmd.set("all_states", 0)

    # show the pocket chain(s) as transparent cartoon
    cmd.show("cartoon", poly)
    cmd.set("cartoon_transparency", 0.72)
    cmd.color("gray80", poly)
    # for a multi-chain (dimer-interface) pocket, tint the protomers with distinct,
    # colourblind-safe pale hues so the two chains are visually separable.
    if multichain:
        _pale = ["palecyan", "wheat", "palegreen", "lightpink"]
        for i, ch in enumerate(sorted(pocket_chains)):
            cmd.color(_pale[i % len(_pale)], f"{poly} and chain {ch}")

    cmd.show("sticks", "ligobj")
    cmd.set("stick_radius", 0.23, "ligobj")
    cmd.util.cbay("ligobj")

    # Key residues — each scoped to its own chain so nothing duplicates.
    key_sel_parts = [f"({_res_sel(r, c)})" for r, c in key_norm]
    if key_sel_parts:
        cmd.select("key", "(" + " or ".join(key_sel_parts) + ")")
        cmd.show("sticks", "key and not name C+N+O")
        cmd.color("salmon", "key and elem C")
        # Label to reduce occlusion/crowding: prefer labelling only the residues
        # that actually H-bond the ligand (the interpretable pharmacophore); if
        # none H-bond, fall back to labelling all key residues. Labels sit at CA.
        # PyMOL has no automatic label repel, so in 3D adjacent residues (e.g.
        # 360/361) collide. We therefore (a) keep H-bonding residues in the order
        # they appear in `key_residues` (already ranked by contact strength) and
        # (b) drop a candidate whose sequence-neighbour in the same chain is
        # already labelled, then (c) cap the total to keep the scene readable.
        # The full residue list still appears in the contact table and 2D figures,
        # so this only trims *3D overview* labels.
        MAX_LABELS = 6
        hb_set = {(hb.get("prot_resseq"), hb.get("prot_chain")) for hb in hbonds
                  if hb.get("prot_resseq")}
        ranked = [rc for rc in key_norm if rc in hb_set] if hb_set else list(key_norm)
        # append any H-bonding residues not already in key order (rare)
        for rc in hb_set:
            if rc not in ranked:
                ranked.append(rc)
        placed = []  # (resseq_int, chain)
        for r, c in ranked:
            if len(placed) >= MAX_LABELS:
                break
            try:
                ri = int(r)
            except (TypeError, ValueError):
                ri = None
            # skip if an immediate sequence neighbour in the same chain is placed
            if ri is not None and any(pc == c and abs(pr - ri) <= 1 for pr, pc in placed):
                continue
            if multichain and c:
                # e.g. ASP25/A vs ASP25/B so the two protomer copies are distinct
                cmd.label(f"{_res_sel(r, c)} and name CA",
                          f'"%s%s/{c}" % (resn, resi)')
            else:
                cmd.label(f"{_res_sel(r, c)} and name CA", f'"%s%s" % (resn, resi)')
            placed.append((ri if ri is not None else -999, c))

    # H-bonds as dashed distance objects, endpoints scoped to the ligand chain.
    # PyMOL computes the actual through-space distance; we only keep dashes at or
    # below `hb_draw_cutoff` so weak/long polar contacts do not render as long
    # lines that read as artifacts. (The full H-bond list still appears in the
    # figures/tables; this only trims the 3D dashes for clarity.)
    hb_draw_cutoff = 3.6
    drawn = 0
    for i, hb in enumerate(hbonds):
        r = hb.get("prot_resseq")
        pa = hb.get("prot_atom")
        la = hb.get("lig_atom")
        pc = hb.get("prot_chain")
        if r is None or pa is None or la is None:
            continue
        a = f"{_res_sel(r, pc)} and name {pa}"
        b = f"ligobj and name {la}"
        try:
            if cmd.count_atoms(a) >= 1 and cmd.count_atoms(b) >= 1:
                d = cmd.get_distance(a, b)
                if d <= hb_draw_cutoff:
                    cmd.distance(f"hb{i}", a, b)
                    drawn += 1
        except Exception:  # noqa: BLE001
            continue
    # dashed appearance (short dashes + gaps so they read as H-bonds, not rods)
    cmd.set("dash_color", "red")
    cmd.set("dash_radius", 0.05)
    cmd.set("dash_length", 0.35)
    cmd.set("dash_gap", 0.45)
    cmd.hide("labels", "hb*")  # overview: no distance numbers (kept for closeup)

    cmd.set("label_size", 13)
    cmd.set("label_color", "black")
    cmd.set("label_outline_color", "white")
    # a translucent white background plate behind each label lifts the text off the
    # coloured sticks/cartoon so residue names stay legible where they overlap.
    try:
        cmd.set("label_bg_color", "white")
        cmd.set("label_bg_transparency", 0.30)
        cmd.set("label_bg_outline", 1)
    except Exception:  # noqa: BLE001
        pass
    # nudge labels off the atom so they don't sit on the sticks, and keep them
    # from being clipped by giving the scene extra margin at zoom time.
    try:
        cmd.set("label_position", (0, 0, 1.4))
    except Exception:  # noqa: BLE001
        pass
    cmd.set("ray_shadows", 0)
    cmd.set("antialias", 2)
    cmd.set("ray_opaque_background", 1)
    cmd.bg_color("white")
    cmd.set("cartoon_fancy_helices", 1)

    # center on the ligand + its key residues (scoped) so nothing clips.
    # imatinib-sized ligands are elongated, so use a generous buffer and also
    # turn off z-clipping so no atom is sliced by the near/far planes.
    focus = "ligobj or key" if key_sel_parts else "ligobj"
    cmd.orient(focus)
    # Fixed-pixel labels on peripheral residues clip at the frame edge if the view
    # is tight. Use a large molecular-space buffer so there is ample screen-space
    # margin for label text around the outermost atoms.
    cmd.zoom(focus, 22.0)
    cmd.clip("slab", 200)
    os.makedirs(os.path.dirname(out_overview), exist_ok=True)
    cmd.ray(2000, 1600)
    cmd.png(out_overview, dpi=200)
    print(f"[OK] wrote {out_overview}")

    if out_closeup:
        # close-up: show H-bond distance labels, zoom onto H-bonding residues +
        # polar ligand atoms only (all scoped to the ligand chain). Give the
        # distance numbers a white halo for contrast over sticks, and use a
        # slightly wider buffer so residue labels are not clipped.
        cmd.show("labels", "hb*")
        cmd.set("label_size", 15)
        cmd.set("label_color", "black")
        cmd.set("label_outline_color", "white")
        try:
            cmd.set("label_bg_color", "white")
            cmd.set("label_bg_transparency", 0.25)
        except Exception:  # noqa: BLE001
            pass
        hb_rc = [(hb.get("prot_resseq"), hb.get("prot_chain")) for hb in hbonds
                 if hb.get("prot_resseq")]
        hb_rc = list(dict.fromkeys(hb_rc))
        if hb_rc:
            hb_parts = " or ".join(f"({_res_sel(r, c)})" for r, c in hb_rc)
            closeup_sel = f"({hb_parts}) or (ligobj and elem N+O)"
        else:
            closeup_sel = "ligobj"
        cmd.orient(closeup_sel)
        cmd.zoom(closeup_sel, 5.0)
        cmd.ray(2000, 1600)
        cmd.png(out_closeup, dpi=200)
        print(f"[OK] wrote {out_closeup}")
    return out_overview


def render_matplotlib_fallback(structure, ligand_residue, key_residues, out_path):
    """Low-fi 3D fallback: scatter ligand heavy atoms + pocket CA atoms."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from Bio.PDB.Polypeptide import is_aa
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

    lig = np.array([a.coord for a in ligand_residue.get_atoms() if a.element != "H"])
    model = list(structure)[0]
    ca = []
    labels = []
    # key_residues may be ints or (resseq, chain) tuples; build a set of both the
    # bare resseq and (resseq, chain) so either form matches.
    keyset = set()
    keyset_rc = set()
    for k in key_residues:
        if isinstance(k, (tuple, list)):
            keyset.add(k[0])
            if len(k) > 1 and k[1]:
                keyset_rc.add((k[0], k[1]))
        else:
            keyset.add(k)
    for chain in model:
        for res in chain:
            if res.id[0] != " " or not is_aa(res, standard=False):
                continue
            match = (res.id[1], chain.id) in keyset_rc if keyset_rc else (res.id[1] in keyset)
            if match and "CA" in res:
                ca.append(res["CA"].coord)
                labels.append(f"{res.resname.strip()}{res.id[1]}")
    ca = np.array(ca) if ca else np.empty((0, 3))

    fig = plt.figure(figsize=(8, 7))
    ax = fig.add_subplot(111, projection="3d")
    ax.scatter(lig[:, 0], lig[:, 1], lig[:, 2], c="#D4A04A", s=60,
               edgecolor="k", label="ligand")
    if len(ca):
        ax.scatter(ca[:, 0], ca[:, 1], ca[:, 2], c="#0279EE", s=45,
                   edgecolor="k", label="pocket C\u03b1")
        for (x, y, z), lab in zip(ca, labels):
            ax.text(x, y, z, lab, fontsize=7, color="#111111")
    ax.set_title("Binding pocket (fallback 3D; install PyMOL for publication view)",
                 fontsize=11)
    ax.legend(loc="upper left", fontsize=9)
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_zlabel("z")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[OK] wrote fallback 3D {out_path}")
    return out_path


def render_pocket_3d(structure, structure_path, ligand_residue, ligand_resname,
                     key_residues, hbonds, out_overview, out_closeup=None,
                     ligand_chain=None):
    """
    Try PyMOL; on any failure, fall back to matplotlib. Returns dict of outputs.
    """
    # residue number of the specific analysed ligand copy (guards against crystals
    # holding several copies of the same ligand code).
    try:
        ligand_resi = ligand_residue.id[1]
    except Exception:  # noqa: BLE001
        ligand_resi = None
    if _have_pymol():
        try:
            render_pymol(structure_path, ligand_resname, key_residues, hbonds,
                         out_overview, out_closeup, ligand_chain, ligand_resi)
            return {"overview": out_overview, "closeup": out_closeup, "engine": "pymol"}
        except Exception as e:  # noqa: BLE001
            print(f"[warn] PyMOL render failed ({e}); using matplotlib fallback")
    fb = render_matplotlib_fallback(structure, ligand_residue, key_residues, out_overview)
    return {"overview": fb, "closeup": None, "engine": "matplotlib"}

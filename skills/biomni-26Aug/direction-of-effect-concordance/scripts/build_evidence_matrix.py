#!/usr/bin/env python3
"""
build_evidence_matrix.py — assemble the evidence matrix and consensus calls from the
structured pulls, applying the fixed direction-mapping rules and the confidence/discordance
logic (references/direction_rules.md).

This is a SCAFFOLD: it seeds votes from the structured Open Targets + DepMap pulls and leaves
placeholders for the human-genetics and mouse-KO votes that require the agent's reading of the
literature (Step 3/4). The agent MUST review/edit data/evidence_matrix.csv — especially
allele-specific gain-of-function cases and the mouse-KO axis — before finalizing.

It reconciles votes -> consensus + confidence tier + strict discordance flags.

Usage:
  python build_evidence_matrix.py --run RUN
  python build_evidence_matrix.py --run RUN --reconcile-only   # after you edit the matrix
"""
import argparse, json, os
import pandas as pd

AXES_DEFAULT = ["Human genetics", "Functional/CRISPR", "Drug MoA", "Mouse KO"]
# Evidence-strength ordering (tie-breaker + tier justification only; NOT numeric weights).
STRENGTH = {"Human genetics": 3, "Drug MoA": 3, "Functional/CRISPR": 2, "Mouse KO": 1}


def seed_matrix(run, axes):
    data_dir = os.path.join(run, "data")
    ot = {}
    otp = os.path.join(data_dir, "opentargets_raw.json")
    if os.path.exists(otp):
        ot = json.load(open(otp)).get("targets", {})
    dm = {}
    dmp = os.path.join(data_dir, "depmap_summary.csv")
    if os.path.exists(dmp):
        for _, r in pd.read_csv(dmp).iterrows():
            dm[r["target"]] = r.to_dict()

    targets = list(ot.keys()) or list(dm.keys())
    rows = []
    for g in targets:
        info = ot.get(g, {})
        ind = info.get("indication", "")
        ens = info.get("ensembl_id", "")
        for axis in axes:
            row = {"target": g, "ensembl_id": ens, "indication": ind, "axis": axis,
                   "raw_readout": "", "vote": "not_informative", "informative": 0,
                   "source": "", "cites": "", "note": "REVIEW: fill from literature"}
            if axis == "Drug MoA" and info:
                drugs = info.get("drugs", [])
                approved = [d["name"] for d in drugs if d.get("max_stage") == "APPROVAL"]
                direction = info.get("drug_moa_direction", "not_informative")
                if drugs:
                    row.update(
                        raw_readout=(f"{len(drugs)} drug candidate(s); "
                                     f"{info.get('n_inhibitor_moa',0)} inhibitor vs "
                                     f"{info.get('n_activator_moa',0)} activator MoA; "
                                     f"approved: {', '.join(approved) if approved else 'none'}"),
                        vote=direction,
                        informative=int(direction != "not_informative"),
                        source="opentargets",
                        note="REVIEW: confirm action types map to direction")
                else:
                    row.update(raw_readout="no drug candidates in Open Targets",
                               note="No drug MoA evidence (itself a finding)")
            elif axis == "Functional/CRISPR" and g in dm:
                d = dm[g]
                interp = str(d.get("interpretation", ""))
                vote = "INHIBIT" if interp.startswith("INHIBIT") else "not_informative"
                row.update(
                    raw_readout=(f"DepMap mean gene-effect={d.get('mean_gene_effect')} "
                                 f"(neg=essential), frac_dependent={d.get('frac_dependent')}"),
                    vote=vote, informative=int(vote != "not_informative"),
                    source="depmap",
                    note="REVIEW: add disease-lineage context + literature")
            elif axis == "Human genetics" and info:
                gs = info.get("indication_genetic_association")
                row.update(
                    raw_readout=(f"OT genetic_association score={gs}" if gs is not None
                                 else "OT genetic_association: n/a"),
                    source="opentargets/literature",
                    note="REVIEW: assign direction from LoF/GoF literature (see direction_rules)")
            elif axis == "Mouse KO" and info:
                mc = info.get("mouse_phenotype_classes", [])
                row.update(
                    raw_readout=(f"{len(mc)} mouse phenotype class(es): "
                                 + ", ".join(c["label"] for c in mc[:6])),
                    source="opentargets/literature",
                    note="REVIEW: silent KO = not_informative (NOT opposing); "
                         "knockdown of GoF allele = INHIBIT")
            rows.append(row)
    return pd.DataFrame(rows)


def reconcile(mat):
    calls = []
    for g, sub in mat.groupby("target", sort=False):
        ind = sub["indication"].iloc[0] if "indication" in sub else ""
        inf = sub[sub["informative"] == 1]
        votes = inf["vote"].tolist()
        n_inf = len(inf)
        if n_inf == 0:
            calls.append(dict(target=g, indication=ind, consensus="CONTESTED",
                              n_informative=0, n_agree=0, concordance="0/0",
                              confidence="Low-Contested", flagged="[]",
                              key_flag="No informative axis."))
            continue
        n_inh = votes.count("INHIBIT")
        n_act = votes.count("ACTIVATE")
        if n_inh and n_act:
            consensus = "CONTESTED"
        else:
            consensus = "INHIBIT" if n_inh >= n_act else "ACTIVATE"
        n_agree = max(n_inh, n_act) if consensus != "CONTESTED" else max(n_inh, n_act)
        # flags: any axis needing interpretation (note starts with 'ALLELE' or contains
        # 'allele-specific'/'toxicity'/'safety'), or any opposing axis
        flagged = []
        for _, r in sub.iterrows():
            note = str(r.get("note", ""))
            if r["informative"] == 1 and r["vote"] not in (consensus, "not_informative") \
                    and consensus != "CONTESTED":
                flagged.append((r["axis"], f"opposes majority ({r['vote']})"))
            elif any(k in note.lower() for k in
                     ["allele-specific", "toxicity", "safety", "context-specific"]):
                flagged.append((r["axis"], note))
        # confidence tier.
        # Denominator = informative axes. An axis flagged for allele-/context-specific
        # interpretation still counts as an informative, concordant vote (e.g. PNPLA3 I148M
        # toxic gain-of-function: benefit is allele-directed) -- it caps the tier at
        # High-Moderate rather than removing it from the consensus. Thinness (few informative
        # axes) softens the tier but only forces Low-Contested when <2 informative axes,
        # because human genetics + one concordant functional/drug axis is a legitimate call.
        opposing = any("opposes majority" in f[1] for f in flagged)
        interp = any(("allele" in f[1].lower() or "context-specific" in f[1].lower())
                     for f in flagged)
        concordant_all = (n_agree == n_inf)  # every informative axis agrees
        if consensus == "CONTESTED" or opposing or n_inf < 2:
            conf = "Low-Contested"
        elif concordant_all and interp:
            conf = "High-Moderate"          # concordant but allele/context caveat
        elif concordant_all and n_inf >= 3:
            conf = "High"                   # >=3 axes, unanimous, no caveat
        elif concordant_all and n_inf == 2:
            conf = "Moderate"               # clean but thin (2 informative axes)
        else:
            conf = "Moderate"
        key_flag = flagged[0][1] if flagged else "None"
        calls.append(dict(target=g, indication=ind, consensus=consensus,
                          n_informative=n_inf, n_agree=n_agree,
                          concordance=f"{n_agree}/{n_inf}", confidence=conf,
                          flagged=str(flagged), key_flag=key_flag))
    return pd.DataFrame(calls)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--axes", default=",".join(AXES_DEFAULT))
    ap.add_argument("--reconcile-only", action="store_true",
                    help="skip seeding; just recompute consensus from an edited matrix")
    args = ap.parse_args()
    data_dir = os.path.join(args.run, "data")
    os.makedirs(data_dir, exist_ok=True)
    mat_path = os.path.join(data_dir, "evidence_matrix.csv")
    axes = [a.strip() for a in args.axes.split(",") if a.strip()]

    if args.reconcile_only:
        if not os.path.exists(mat_path):
            raise SystemExit(f"{mat_path} not found; run without --reconcile-only first.")
        mat = pd.read_csv(mat_path).fillna("")
    else:
        mat = seed_matrix(args.run, axes)
        mat.to_csv(mat_path, index=False)
        print(f"Seeded evidence matrix -> {mat_path}")
        print("ACTION REQUIRED: review/edit votes, informative flags, cites, and notes "
              "(esp. Human genetics + Mouse KO), then re-run with --reconcile-only.")

    calls = reconcile(mat)
    calls_path = os.path.join(data_dir, "consensus_calls.csv")
    calls.to_csv(calls_path, index=False)
    print(f"Consensus calls -> {calls_path}")
    for _, r in calls.iterrows():
        print(f"  {r['target']}: {r['consensus']} ({r['concordance']}, {r['confidence']})")


if __name__ == "__main__":
    main()

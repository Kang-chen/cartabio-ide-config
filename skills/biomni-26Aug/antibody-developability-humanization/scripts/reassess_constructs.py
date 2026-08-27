#!/usr/bin/env python3
"""
reassess_constructs.py  --  Re-run the 3 assessment axes on every construct and
build the MASTER frontier table (developability x immunogenicity x humanness).

Given a set of constructs {name: {"VH":.., "VL":.., "label":..}}, this:
  1. Developability: total & CDR liabilities, weighted burden, N-glyco sites,
     plus per-chain biophysical properties (pI, charge, GRAVY, aromaticity).
  2. Immunogenicity: MHC-II epitope load / promiscuous epitopes over the HLA-DR
     panel (via immunogenicity_mhcii; degrades gracefully to 'unavailable').
  3. Humanness: framework % identity to nearest human germline (VH & VL).
The MASTER table lets you read the "frontier": naive graft = most human & least
immunogenic; back-mutation trades humanness/immunogenicity for likely affinity.

Public API:
  reassess(constructs, scheme=, dr_panel=, run_immunogenicity=True) -> dict of DataFrames
  developability_table(constructs, scheme=)
  humanness_table(constructs, scheme=)
  master_table(dev_df, immuno_fv_df, human_df)

CLI:
  python reassess_constructs.py --constructs constructs.json [--scheme kabat]
      [--no-immuno] [--outdir tables/]
"""
from __future__ import annotations
import argparse, json, os
import pandas as pd

from ab_core import (make_chain, scan_liabilities, biophysical,
                     framework_identity_to_human, DEFAULT_SCHEME, DR_PANEL_7)
from developability_scan import scan_construct
import immunogenicity_mhcii as imm


# ---------------------------------------------------------------------------
# Axis 1: developability (liabilities + biophysical)
# ---------------------------------------------------------------------------
def developability_table(constructs: dict, scheme: str = DEFAULT_SCHEME):
    """Returns (summary_df, per_liability_df, biophysical_df, apr_df).
    scan_construct(name, vh, vl, scheme) ->
        (motif_df, rollup_dict, bio_df, apr_df). The rollup now also carries the
    named AGGRESCAN aggregation metric (agg_score_Fv, n_APR, APR_in_CDR/FR,
    agg_weighted); apr_df lists the per-construct aggregation-prone regions."""
    summ_rows, liab_frames, bio_frames, apr_frames = [], [], [], []
    for name, v in constructs.items():
        motif_df, rollup, bio_df, apr_df = scan_construct(
            name, v.get("VH"), v.get("VL"), scheme=scheme)
        rollup = dict(rollup)
        rollup["label"] = v.get("label", "")
        summ_rows.append(rollup)
        if len(motif_df):
            liab_frames.append(motif_df)
        if len(bio_df):
            bio_frames.append(bio_df)
        if len(apr_df):
            apr_frames.append(apr_df)
    summ = pd.DataFrame(summ_rows)
    # keep label as 2nd column for readability
    if "label" in summ.columns:
        cols = ["construct", "label"] + [c for c in summ.columns
                                         if c not in ("construct", "label")]
        summ = summ[cols]
    liab = (pd.concat(liab_frames, ignore_index=True)
            if liab_frames else pd.DataFrame())
    bio = (pd.concat(bio_frames, ignore_index=True)
           if bio_frames else pd.DataFrame())
    apr = (pd.concat(apr_frames, ignore_index=True)
           if apr_frames else pd.DataFrame())
    return summ, liab, bio, apr


# ---------------------------------------------------------------------------
# Axis 3: humanness (framework identity to nearest human germline)
# ---------------------------------------------------------------------------
def humanness_table(constructs: dict, scheme: str = DEFAULT_SCHEME):
    rows = []
    for name, v in constructs.items():
        vh_gene = vh_fri = vl_gene = vl_fri = None
        if v.get("VH"):
            vh_gene, vh_fri = framework_identity_to_human(v["VH"], scheme)
        if v.get("VL"):
            vl_gene, vl_fri = framework_identity_to_human(v["VL"], scheme)
        mean_fri = None
        vals = [x for x in (vh_fri, vl_fri) if x is not None]
        if vals:
            mean_fri = round(sum(vals) / len(vals), 1)
        rows.append({"construct": name, "label": v.get("label", ""),
                     "VH_human_germline": vh_gene, "VH_FR_identity_%": vh_fri,
                     "VL_human_germline": vl_gene, "VL_FR_identity_%": vl_fri,
                     "mean_FR_humanness_%": mean_fri})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Axis 2: immunogenicity (per chain -> Fv aggregate)
# ---------------------------------------------------------------------------
def immunogenicity_tables(constructs: dict, scheme: str = DEFAULT_SCHEME,
                          dr_panel=None):
    """Returns (fv_df, per_chain_df, status, reason, predictor).

    `predictor` is the tier that actually ran ('local' | 'iedb' | None) so the
    report can attribute the correct data source. Calls assess_immunogenicity()
    ONCE on the whole construct dict (it takes {name:{VH,VL}} and returns
    per-chain 'summary' rows keyed by chain name '<construct>_<VH|VL>'). We then
    roll per-chain loads up to the Fv level. If no predictor is available,
    status='unavailable' and loads are None (never fabricated)."""
    dr_panel = dr_panel or DR_PANEL_7
    labels = {name: v.get("label", "") for name, v in constructs.items()}

    res = imm.assess_immunogenicity(constructs, alleles=dr_panel, scheme=scheme)
    if res.get("status") != "ok":
        # degraded: emit Fv rows with None loads + the specific reason
        fv_rows = [{"construct": n, "label": labels[n], "status": "unavailable",
                    "Fv_n_15mers": None, "Fv_epitope_load": None,
                    "Fv_strong_load": None, "Fv_promiscuous": None,
                    "Fv_strong_promiscuous": None, "promisc_in_CDR": None,
                    "promisc_in_FR": None} for n in constructs]
        return (pd.DataFrame(fv_rows), pd.DataFrame(),
                "unavailable", res.get("reason"), None)

    per_chain = res["summary"].copy()   # columns: chain, n_15mers, ...
    # split chain -> construct + domain
    def _split(ch):
        for dom in ("VH", "VL"):
            if ch.endswith("_" + dom):
                return ch[: -(len(dom) + 1)], dom
        return ch, ""
    per_chain[["construct", "domain"]] = per_chain["chain"].apply(
        lambda c: pd.Series(_split(c)))

    agg_cols = ["n_15mers", "epitope_load", "strong_load", "promiscuous",
                "strong_promiscuous", "promisc_in_CDR", "promisc_in_FR"]
    fv_rows = []
    for name in constructs:
        sub = per_chain[per_chain["construct"] == name]
        row = {"construct": name, "label": labels[name], "status": "ok"}
        row["Fv_n_15mers"] = int(sub["n_15mers"].sum())
        row["Fv_epitope_load"] = int(sub["epitope_load"].sum())
        row["Fv_strong_load"] = int(sub["strong_load"].sum())
        row["Fv_promiscuous"] = int(sub["promiscuous"].sum())
        row["Fv_strong_promiscuous"] = int(sub["strong_promiscuous"].sum())
        row["promisc_in_CDR"] = int(sub["promisc_in_CDR"].sum())
        row["promisc_in_FR"] = int(sub["promisc_in_FR"].sum())
        fv_rows.append(row)
    return pd.DataFrame(fv_rows), per_chain, "ok", None, res.get("predictor")


# ---------------------------------------------------------------------------
# MASTER frontier table
# ---------------------------------------------------------------------------
def master_table(dev_summ: pd.DataFrame, fv_immuno: pd.DataFrame,
                 human_df: pd.DataFrame):
    dev_cols = ["construct", "label", "total_liabilities", "CDR_liabilities",
                "total_weighted_burden", "N_glyco_sites",
                # named aggregation metric (AGGRESCAN a3v)
                "agg_score_Fv", "n_APR", "APR_in_CDR", "APR_in_FR",
                "agg_weighted"]
    m = dev_summ[[c for c in dev_cols if c in dev_summ.columns]].copy()
    imm_cols = ["construct", "Fv_epitope_load", "Fv_promiscuous",
                "promisc_in_FR", "promisc_in_CDR"]
    if len(fv_immuno):
        m = m.merge(fv_immuno[[c for c in imm_cols if c in fv_immuno.columns]],
                    on="construct", how="left")
    hum_cols = ["construct", "VH_FR_identity_%", "VL_FR_identity_%",
                "mean_FR_humanness_%"]
    m = m.merge(human_df[[c for c in hum_cols if c in human_df.columns]],
                on="construct", how="left")
    return m


def reassess(constructs: dict, scheme: str = DEFAULT_SCHEME,
             dr_panel=None, run_immunogenicity: bool = True):
    """Run all three axes; return dict of DataFrames + immunogenicity status."""
    dev_summ, dev_liab, dev_bio, dev_apr = developability_table(constructs, scheme)
    human_df = humanness_table(constructs, scheme)
    if run_immunogenicity:
        fv_immuno, immuno_chain, immuno_status, immuno_reason, immuno_predictor = \
            immunogenicity_tables(constructs, scheme, dr_panel)
    else:
        fv_immuno, immuno_chain, immuno_status, immuno_reason, immuno_predictor = (
            pd.DataFrame(), pd.DataFrame(), "skipped", None, None)
    master = master_table(dev_summ, fv_immuno, human_df)
    return {"developability_summary": dev_summ, "liabilities": dev_liab,
            "biophysical": dev_bio, "aggregation_aprs": dev_apr,
            "humanness": human_df,
            "immunogenicity_fv": fv_immuno, "immunogenicity_chain": immuno_chain,
            "immunogenicity_status": immuno_status,
            "immunogenicity_reason": immuno_reason,
            "immunogenicity_predictor": immuno_predictor, "master": master}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Re-assess constructs -> MASTER frontier table")
    ap.add_argument("--constructs", required=True,
                    help="JSON: {name:{VH,VL,label}} (e.g. from humanize --json)")
    ap.add_argument("--scheme", default=DEFAULT_SCHEME)
    ap.add_argument("--no-immuno", action="store_true",
                    help="skip MHC-II immunogenicity axis")
    ap.add_argument("--outdir", default=None, help="write CSVs here")
    args = ap.parse_args()

    with open(args.constructs) as f:
        raw = json.load(f)
    # accept either {name:{VH,VL}} or humanize() JSON with 'constructs' key
    constructs = raw.get("constructs", raw)
    # normalize: keep only VH/VL/label
    constructs = {k: {"VH": v.get("VH"), "VL": v.get("VL"),
                      "label": v.get("label", "")}
                  for k, v in constructs.items()}

    res = reassess(constructs, scheme=args.scheme,
                   run_immunogenicity=not args.no_immuno)

    print("=== MASTER frontier (developability x immunogenicity x humanness) ===")
    print(res["master"].to_string(index=False))
    print(f"\nImmunogenicity axis status: {res['immunogenicity_status']}")

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
        res["master"].to_csv(os.path.join(args.outdir, "MASTER_comparison.csv"),
                             index=False)
        res["developability_summary"].to_csv(
            os.path.join(args.outdir, "liability_summary.csv"), index=False)
        res["humanness"].to_csv(os.path.join(args.outdir, "humanness.csv"),
                                index=False)
        res["biophysical"].to_csv(os.path.join(args.outdir, "biophysical.csv"),
                                  index=False)
        if len(res.get("aggregation_aprs", [])):
            res["aggregation_aprs"].to_csv(
                os.path.join(args.outdir, "aggregation_APRs.csv"), index=False)
        if len(res["immunogenicity_fv"]):
            res["immunogenicity_fv"].to_csv(
                os.path.join(args.outdir, "immuno_fv_summary.csv"), index=False)
        print(f"\nWrote CSVs to {args.outdir}")


if __name__ == "__main__":
    main()

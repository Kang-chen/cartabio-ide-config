#!/usr/bin/env python3
"""
run_pipeline.py  --  End-to-end orchestrator for the antibody
developability / immunogenicity / humanization skill.

This is the EXECUTABLE SOURCE OF TRUTH for how the individual scripts fit
together. SKILL.md and the reference guides mirror the calls made here, so the
documented interfaces cannot drift from the code.

Pipeline (branch-aware):
  ingest -> species/format gate -> [humanize if non-human] -> reassess
  (developability + humanness + MHC-II, degrading if no predictor)
  -> [benchmark if a reference is supplied] -> figures -> dual-mode PDF report

Typical uses
------------
  # bundled demo, murine, reference-present benchmark
  python run_pipeline.py --example mumab4d5 --outdir /mnt/results/ab_4d5

  # bundled demo, already-human, assess-only
  python run_pipeline.py --example adalimumab --outdir /mnt/results/ab_ada

  # arbitrary antibody from FASTA (no reference -> reference-absent default)
  python run_pipeline.py --fasta my_ab.fasta --name "my mAb" \
      --outdir /mnt/results/ab_mine

  # override the HLA-DR panel / numbering / back-mutation aggressiveness
  python run_pipeline.py --example mumab4d5 --outdir out \
      --dr-panel HLA-DRB1*04:01,HLA-DRB1*15:01 --scheme kabat --level moderate

If no MHC-II predictor is reachable, pass --no-immuno (or let it auto-skip)
and the immunogenicity axis is reported as unavailable rather than fabricated.
"""
from __future__ import annotations
import argparse, json, os, sys

# make sibling scripts importable regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import ab_core
import species_format_gate
import humanize_backmutate
import reassess_constructs
import benchmark_reveal
import make_figures
import build_report
import load_example_data
import ingest_sequences

DEFAULT_DR_PANEL = ["HLA-DRB1*01:01", "HLA-DRB1*03:01", "HLA-DRB1*04:01",
                    "HLA-DRB1*07:01", "HLA-DRB1*08:01", "HLA-DRB1*11:01",
                    "HLA-DRB1*15:01"]


class _NpEnc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, np.integer): return int(o)
        if isinstance(o, np.floating): return float(o)
        if isinstance(o, np.ndarray): return o.tolist()
        if isinstance(o, np.bool_): return bool(o)
        return super().default(o)


def run(vh, vl, name, outdir, reference=None, reference_name=None,
        canonical=None, source_species=None, dr_panel=None, scheme="kabat",
        level="conservative", run_immunogenicity=True, predictor_label=None):
    """Run the full branch-aware pipeline. Returns a dict of artifacts."""
    os.makedirs(outdir, exist_ok=True)
    figdir = os.path.join(outdir, "figures")
    os.makedirs(figdir, exist_ok=True)
    dr_panel = dr_panel or DEFAULT_DR_PANEL

    # -- 1. species / format gate --
    gate = species_format_gate.gate(vh, vl, scheme=scheme)
    branch = gate["branch"]
    print(f"[gate] {name}: branch={branch} (do_humanize={gate['do_humanize']})")

    humanize_out = None
    benchmark_out = None

    if branch == "single_domain":
        # assess the single chain as-is (no pairing, no humanization)
        constructs = {name: {"VH": vh or "", "VL": vl or "",
                             "label": "single domain"}}
    elif branch == "paired_human":
        # already human -> assess only
        constructs = {name: {"VH": vh, "VL": vl, "label": "as-is (human)"}}
    else:
        # non-human -> humanize (compare acceptor philosophies)
        humanize_out = humanize_backmutate.humanize(vh, vl, scheme=scheme,
                                                    level=level)
        constructs = humanize_out["constructs"]
        print(f"[humanize] constructs: {list(constructs)}")

    # -- 2. reassess (developability + humanness + MHC-II) --
    re_out = reassess_constructs.reassess(
        constructs, scheme=scheme, dr_panel=dr_panel,
        run_immunogenicity=run_immunogenicity)
    print(f"[reassess] immunogenicity status: {re_out['immunogenicity_status']}")

    # -- 3. optional reference-present benchmark --
    if reference and branch == "paired_nonhuman" and humanize_out is not None:
        # Featured lead selection.
        #   With a validated reference in hand, the meaningful lead is the
        #   back-mutated design that lands CLOSEST to the reference (that is the
        #   whole point of a convergence test) -- rank bmut constructs by VH
        #   identity to the reference, not by absolute humanness. (Absolute
        #   humanness would prefer the most-germline design, which need not be
        #   the one that reproduces the clinical answer.)
        master = re_out["master"]
        bmut_keys = [c for c in master["construct"] if "bmut" in c]
        lead_key = None
        if bmut_keys:
            ident = benchmark_reveal.identity_vs_reference(
                constructs, reference["VH"], reference["VL"],
                design_keys=bmut_keys)
            lead_key = (ident.sort_values("VH_vs_ref_%", ascending=False)
                        .iloc[0]["construct"])
        graft_key = (lead_key.replace("bmut", "graft")
                     if lead_key and lead_key.replace("bmut", "graft")
                     in set(master["construct"]) else None)
        if lead_key and graft_key:
            benchmark_out = benchmark_reveal.benchmark(
                constructs, reference["VH"], reference["VL"],
                lead_key=lead_key, graft_key=graft_key, scheme=scheme,
                canonical=canonical,
                ref_name=reference_name or reference.get("name", "reference"))
            sc = benchmark_out["scores"]
            print(f"[benchmark] lead={sc['lead']} recovery="
                  f"{sc['canonical_recovery']} concordant="
                  f"{sc['n_concordant']}/{sc['n_backmutations']}")

    # -- 4. figures (mode/degradation-aware) --
    figs = make_figures.make_all(
        master=re_out["master"], dev_summ=re_out["developability_summary"],
        fv_immuno=re_out["immunogenicity_fv"],
        immuno_status=re_out["immunogenicity_status"],
        humanness=re_out["humanness"], outdir=figdir,
        ref_key=None, benchmark=benchmark_out)
    print(f"[figures] {list(figs)}")

    # -- 5. serialize payload + build the PDF report --
    payload = build_report.serialize_payload(
        antibody_name=name, reassess_out=re_out, humanize_out=humanize_out,
        benchmark_out=benchmark_out, figures=figs, dr_panel=dr_panel,
        predictor=predictor_label, source_species=source_species,
        reference_name=(reference_name or
                        (reference.get("name") if reference else None)))
    payload_path = os.path.join(outdir, "report_payload.json")
    with open(payload_path, "w") as f:
        json.dump(payload, f, cls=_NpEnc)

    pdf_path = os.path.join(outdir, f"report_{_slug(name)}.pdf")
    build_report.build_report(payload, pdf_path)
    info = build_report.validate_pdf(pdf_path)
    print(f"[report] {pdf_path}  ({info['pages']} pages, {info['bytes']} bytes)")

    # also drop the master table as CSV for downstream use
    csv_path = os.path.join(outdir, "master_frontier.csv")
    re_out["master"].to_csv(csv_path, index=False)

    return {"branch": branch, "mode": payload["mode"], "master": re_out["master"],
            "reassess": re_out, "humanize": humanize_out,
            "benchmark": benchmark_out, "figures": figs,
            "payload_path": payload_path, "pdf_path": pdf_path,
            "csv_path": csv_path}


def _slug(s):
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_")


def _from_example(key):
    ex = load_example_data.get_example(key)
    ref = ex.get("reference")
    return dict(vh=ex["VH"], vl=ex["VL"], name=ex["name"],
                reference=ref, reference_name=(ref or {}).get("name"),
                canonical=ex.get("reference_canonical"),
                source_species=ex.get("source_species"))


def main():
    ap = argparse.ArgumentParser(description="End-to-end antibody assessment/humanization")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--example", help="bundled example: mumab4d5 | adalimumab")
    src.add_argument("--fasta", help="FASTA with VH and VL (2 records)")
    ap.add_argument("--name", help="antibody name (for --fasta)")
    ap.add_argument("--vh", help="VH sequence (alternative to --fasta)")
    ap.add_argument("--vl", help="VL sequence (alternative to --fasta)")
    ap.add_argument("--ref-vh", help="reference VH (enables benchmark)")
    ap.add_argument("--ref-vl", help="reference VL")
    ap.add_argument("--ref-name", default="reference")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--dr-panel", help="comma-separated HLA-DR alleles")
    ap.add_argument("--scheme", default="kabat", choices=["kabat", "imgt"])
    ap.add_argument("--level", default="conservative",
                    choices=list(humanize_backmutate.BACKMUT_LEVELS)
                    if hasattr(humanize_backmutate, "BACKMUT_LEVELS")
                    else ["aggressive", "moderate", "conservative", "maximal"])
    ap.add_argument("--no-immuno", action="store_true",
                    help="skip MHC-II axis (report it as unavailable)")
    ap.add_argument("--predictor-label", default="IEDB NetMHCIIpan-4")
    a = ap.parse_args()

    dr = [x.strip() for x in a.dr_panel.split(",")] if a.dr_panel else None

    if a.example:
        kw = _from_example(a.example)
    else:
        if a.fasta:
            with open(a.fasta) as fh:
                fasta_text = fh.read()
            ing = ingest_sequences.ingest(fasta_text=fasta_text, name=a.name)
            vh, vl = ing["VH"], ing["VL"]
        else:
            vh, vl = a.vh, a.vl
        ref = None
        if a.ref_vh and a.ref_vl:
            ref = {"name": a.ref_name, "VH": a.ref_vh, "VL": a.ref_vl}
        kw = dict(vh=vh, vl=vl, name=a.name or "antibody", reference=ref,
                  reference_name=a.ref_name if ref else None, canonical=None,
                  source_species=None)

    run(outdir=a.outdir, dr_panel=dr, scheme=a.scheme, level=a.level,
        run_immunogenicity=not a.no_immuno, predictor_label=a.predictor_label,
        **kw)


if __name__ == "__main__":
    main()

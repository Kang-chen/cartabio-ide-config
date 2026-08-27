---
id: "skill_1428c3775e3d807bc46446cc90c2a6a0"
name: antibody-developability-humanization
description: "Use to assess or humanize existing mAb, IgG, Fv, scFv, VHH, or nanobody sequences. Covers sequence liabilities and developability, MHC-II/HLA-DR immunogenicity, CDR grafting onto human germlines, framework back-mutations, and humanness scoring."
category: "molecular_design"
visibility: "public"
starting-prompt: "Assess developability and humanize my monoclonal antibody from its VH/VL sequences."
---

# Antibody humanization & liability assessment

End-to-end, sequence-based assessment and humanization of a monoclonal antibody.
Given a VH + VL (or a single chain), it runs four always-on analyses —
**developability liabilities**, **aggregation propensity** (named AGGRESCAN a3v
predictor), **MHC-II immunogenicity**, and (for non-human antibodies)
**humanization with acceptor comparison** — and emits a Phylo-branded PDF report
plus a machine-readable master table. It requires **no clinical reference**; a
blind benchmark against a known reference antibody is an *optional* add-on, not
the default.

**Aggregation is scored with a named predictor, not a surrogate.** Aggregation
risk uses the published **AGGRESCAN** aggregation-propensity scale (a3v; Sanchez
de Groot et al., *FEBS J* 2006 / Conchillo-Sole et al., *BMC Bioinformatics*
2007) — an experimentally-derived, sequence-based scale — to compute a windowed
aggregation profile, call aggregation-prone regions (APRs; runs of >=5 residues
above the hot-spot threshold with no proline, since AGGRESCAN treats proline as
an aggregation breaker), and roll up a CDR-weighted aggregation burden. GRAVY hydrophobicity / pI / net charge are still
reported but only as *descriptive context*, never as the aggregation metric. This
is deliberately a **sequence-based** predictor (no 3D structure required, keeping
the skill structure-free and fully reproducible); it does **not** model 3D
spatial aggregation patches or colloidal effects. When a folded Fv is available,
a **structure-based** predictor is the recommended upgrade — see
"Aggregation: sequence-based vs structure-based" below.

## When to use this skill

Use it for any antibody-sequence engineering question, e.g.:
- "Is this antibody developable / what are its liabilities?"
- "Humanize this murine antibody / graft these CDRs onto a human framework."
- "How immunogenic is this sequence / what are its T-cell epitopes?"
- "Compare humanization strategies (nearest germline vs consensus framework)."
- "Score the framework humanness of these constructs."

It auto-detects the input type and does the right thing:
- **non-human paired Fv** -> humanize (compare acceptors) + assess all constructs
- **already-human paired Fv** -> assess only (no humanization proposed)
- **single domain (VHH / lone VH or VL)** -> assess as-is

## Setup (once per environment)

The antibody-numbering stack is **not** in the default image. Install it first:

```bash
bash scripts/00_setup_env.sh
```

This installs ANARCI + abnumber (numbering), HMMER (ANARCI backend), pyteomics
(pI/charge), and confirms biopython/pandas/reportlab/pypdf are present.
**NetMHCIIpan is licensed and never auto-downloaded** — if a local install
exists, point at it with `export NETMHCIIPAN_BIN=/path/to/netMHCIIpan`; otherwise
the immunogenicity step uses the IEDB web API, and if there is no egress it
marks that section unavailable rather than fabricating numbers.

## Fastest path — the orchestrator

`scripts/run_pipeline.py` runs the whole pipeline (gate -> ingest -> humanize ->
reassess -> benchmark? -> figures -> PDF). Run scripts from the `scripts/`
directory (they import each other by module name).

```bash
cd scripts

# Bundled demo A: murine muMAb 4D5, reference-present benchmark vs trastuzumab
python run_pipeline.py --example mumab4d5 --outdir /path/out_4d5

# Bundled demo B: adalimumab (already human) -> assess-only
python run_pipeline.py --example adalimumab --outdir /path/out_ada

# Your antibody from a FASTA with two records (VH then VL):
python run_pipeline.py --fasta my_ab.fasta --name MyAb --outdir /path/out_myab

# Or pass sequences directly:
python run_pipeline.py --vh "EVQL..." --vl "DIQM..." --name MyAb --outdir /path/out

# Optional reference-present benchmark (only if you have a validated counterpart):
python run_pipeline.py --fasta my_ab.fasta --name MyAb \
    --ref-vh "EVQL..." --ref-vl "DIQM..." --ref-name trastuzumab \
    --outdir /path/out

# Fast developability-only pass (skip MHC-II, report axis as unavailable):
python run_pipeline.py --example adalimumab --no-immuno --outdir /path/out
```

Useful flags: `--level {aggressive|moderate|conservative|maximal}` (back-mutation
aggressiveness, default `conservative`; note "aggressive" = FEWEST back-mutations
= most human), `--scheme {kabat|imgt}` (default kabat), `--dr-panel` (comma-
separated HLA-DR alleles; default is the 7-allele IEDB reference set).

Outputs written to `--outdir`: `report_<name>.pdf`, `master_frontier.csv`,
`payload_<name>.json`, and a `figures/` folder.

## Programmatic use

```python
import run_pipeline
res = run_pipeline.run(
    vh=MY_VH, vl=MY_VL, name="MyAb", outdir="/path/out",
    reference=None,           # or {"name":..., "VH":..., "VL":...} for benchmark
    level="conservative", scheme="kabat",
    run_immunogenicity=True,  # False -> degrade honestly, no epitope numbers
)
# res -> {branch, mode, master (DataFrame), reassess, humanize, benchmark,
#         pdf_path, csv_path, payload_path, figures}
```

## The pipeline modules (verified interfaces)

Call these directly only for custom workflows; otherwise use the orchestrator.

- `species_format_gate.gate(vh, vl, scheme)` -> `{branch, do_humanize, do_assess, ...}`
  where `branch` in `{paired_nonhuman, paired_human, single_domain}`.
- `ingest_sequences.ingest(vh=None, vl=None, fasta_text=None, name=...)` ->
  `{VH, VL, warnings, source, needs_confirmation}` (pass FASTA **content**, not a path).
- `humanize_backmutate.humanize(vh, vl, scheme="kabat", level="conservative")` ->
  `{constructs, backmutations, acceptors, derived_acceptors, scheme, level}`.
  Builds `donor`, `hu_consensus_graft`, `hu_consensus_bmut`, `hu_nearest_graft`,
  `hu_nearest_bmut`.
- `ab_core.aggregation_scan(chain, chain_name, cdr_weight=1.6)` ->
  `(apr_df, rollup)`. Named AGGRESCAN a3v aggregation assessment for one domain:
  computes the windowed a4v profile, calls APRs (runs of >=5 residues above the
  hot-spot threshold), and rolls up `agg_score, n_APR, APR_in_CDR, APR_in_FR,
  agg_weighted, max_a4v, window`. `ab_core.aggregation_profile(seq)` exposes the
  raw `(a4v, window)`; `A3V`, `A3V_HST`, `MIN_APR_LEN` are the scale/thresholds.
- `reassess_constructs.reassess(constructs, scheme="kabat", dr_panel=None, run_immunogenicity=True)`
  -> keys incl. `developability_summary, liabilities, biophysical,
  aggregation_aprs, humanness, immunogenicity_fv, immunogenicity_chain,
  immunogenicity_status, immunogenicity_reason, master`. The `master` frontier
  now also carries the aggregation columns `agg_score_Fv, n_APR, APR_in_CDR,
  APR_in_FR, agg_weighted`, and `aggregation_aprs` is the per-APR detail table.
  (Note: `developability_scan.scan_construct` / `scan_all` now return an extra
  trailing `apr_df` element.)
- `benchmark_reveal.benchmark(constructs, ref_vh, ref_vl, lead_key, graft_key, scheme, canonical, ref_name)`
  -> `{identity, concordance, scores}`. Also `identity_vs_reference(constructs, ref_vh, ref_vl, design_keys=None)`.
- `make_figures.make_all(master, dev_summ, fv_immuno, immuno_status, humanness, outdir, order, ref_key, benchmark)`
  -> `{fig_name: [png, svg]}`.
- `build_report.serialize_payload(...)` then `build_report.build_report(payload, out_path)`;
  `build_report.validate_pdf(path)` sanity-checks the result.
- `load_example_data.get_example(name)` — bundled examples (`mumab4d5`,
  `adalimumab`; aliases `4d5`, `humira`).

## Report modes (see `references/report-modes-guide.md`)

- **reference_absent (DEFAULT)** — no benchmark language at all.
- **reference_present (OPTIONAL)** — adds a blind convergence test vs a supplied
  reference (identity, back-mutation concordance, canonical recovery). The
  benchmark only *scores* a blind design; it never *chooses* it.
- The report is also **branch-aware**: already-human input yields an assess-only
  report that states no humanization is proposed; non-human input yields a
  grafting report. When the MHC-II axis is unavailable the report says so and
  shows no epitope numbers.

## Aggregation: sequence-based (default) vs structure-based (upgrade)

**Why sequence-based is the default here.** The whole skill is designed to run
from sequence alone (no folding step), which keeps it fast, deterministic, fully
reproducible, and free of any structure-prediction/FoldX dependency. The
AGGRESCAN a3v predictor fits that design: it is a *named, experimentally-derived,
peer-reviewed* aggregation scale (unlike the GRAVY/charge surrogates it replaces),
yet it needs only the linear sequence. For a first-pass developability triage and
for comparing humanized constructs against the parent, this is the right tool.

**Its honest limitation.** A purely sequence-based scale cannot see the folded
structure: it may flag aggregation-prone stretches that are actually buried in the
native fold, and it misses 3D aggregation *patches* formed by residues far apart
in sequence but close in space, as well as colloidal/charge-network effects. The
report states this explicitly and never over-claims.

**The structure-based upgrade path (optional, when a folded Fv exists).** For a
confirmatory, structure-aware read, run a structure-based predictor on a predicted
or experimental Fv:
- **AggreScan3D (A3D)** — the structure-based extension of this exact a3v scale
  (Kuriata et al., *NAR* 2019). `pip install aggrescan3d`; needs a PDB (fold the
  Fv first, e.g. via the HPC folding tools ESMFold/Boltz-2 available in this
  environment), and FoldX for its dynamic/mutation mode (static mode works on a
  fixed model).
- **TAP (Therapeutic Antibody Profiler)** — antibody-specific developability
  flags incl. patches of surface hydrophobicity, via SAbPred (web); also needs an
  Fv model.
These are documented rather than run by default because they add a structure step;
wire one in when a structure is on hand or 3D-patch confirmation is required.

## Reference guides (read before non-trivial changes)

- `references/humanization-guide.md` — acceptor philosophies (consensus vs
  nearest), Vernier/interface/canonical position sets, back-mutation levels.
- `references/liability-rules.md` — the exact motif set, severities, and 1.6x
  CDR weighting.
- `references/aggregation-rules.md` — the AGGRESCAN a3v scale, hot-spot
  threshold, APR calling, window sizing, CDR-weighting, and the sequence-vs-
  structure scope.
- `references/immunogenicity-guide.md` — the 3-tier predictor, epitope-calling
  thresholds, honest degradation, and the HLA-DR panel.
- `references/report-modes-guide.md` — the four report configurations and the
  lead-selection policy.

## Guardrails

- **Never fabricate immunogenicity numbers.** If no predictor is available, the
  axis is reported as unavailable and ranking uses developability + humanness only.
- **Aggregation uses the named AGGRESCAN a3v scale, not GRAVY/charge.** Do not
  silently substitute a hydrophobicity/charge surrogate for the aggregation
  metric; GRAVY/pI/charge are context only. Do not re-tune the a3v values, the
  hot-spot threshold, or the >=5-residue APR rule without noting the deviation.
  Report the sequence-based scope honestly and do not present it as a
  structure-aware 3D-patch result.
- **Keep the benchmark blind.** Humanization is built without reference to any
  known counterpart; the reference is used only for post-hoc scoring in
  reference-present mode.
- **CDRs are grafted verbatim; back-mutations touch framework only** (Vernier /
  interface / canonical positions), never CDRs.
- Compare constructs only when scanned against the same HLA-DR panel and scheme.

## Data sources & attribution

MHC-II epitope predictions use the **Immune Epitope Database (IEDB)**. Please
acknowledge and cite it when you present results (commercial use is permitted; a
citation is expected):

> Epitope data and MHC-II binding predictions from the Immune Epitope Database
> (IEDB, https://www.iedb.org), a free public resource funded by NIAID.

- IEDB database: Vita R, Blazeska N, Marrama D, et al. The Immune Epitope
  Database (IEDB): 2024 update. *Nucleic Acids Res.* 2025 Jan 6;53(D1):D436-D443.
  doi:10.1093/nar/gkae1092.
- IEDB prediction tools (used here for MHC-II binding): Dhanda SK, Mahajan S,
  Paul S, et al. IEDB-AR: immune epitope database-analysis resource in 2019.
  *Nucleic Acids Res.* 2019 Jul 2;47(W1):W502-W506. doi:10.1093/nar/gkz452.

**Data handling — read before running proprietary sequences.** When no local
NetMHCIIpan is installed, the immunogenicity step falls back to the **hosted IEDB
web API and transmits your input VH/VL sequences to iedb.org** for prediction. To
keep sequences on-machine, install NetMHCIIpan locally and point the skill at it
with `export NETMHCIIPAN_BIN=/path/to/netMHCIIpan` (the local predictor is always
preferred over the API). See `NOTICES.md` for the full data-flow description.

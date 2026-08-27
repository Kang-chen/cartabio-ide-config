# MHC-II (HLA-DR) T-cell epitope assessment

Implemented in `scripts/immunogenicity_mhcii.py`. This module estimates the
CD4+ T-cell (helper) epitope load of each construct by predicting binding of
overlapping 15-mer peptides to a panel of HLA-DR alleles. High MHC-II binding in
the framework is a classic immunogenicity risk for a non-human antibody and is
one of the main things humanization is meant to reduce; binding in the CDRs is
harder to remove because the CDRs carry the binding function.

## The three-tier predictor (degrading by design)

The single most important design property of this module: **it degrades honestly
when no licensed predictor is available, rather than fabricating numbers.**

1. **Local NetMHCIIpan (preferred).** Used if a customer install is present —
   either the env var `NETMHCIIPAN_BIN` points at the executable, or
   `netMHCIIpan` is on `PATH`. This is the gold-standard, license-restricted
   predictor and is what a pharma customer will typically have on-prem.
2. **IEDB web API (`netmhciipan_el`).** Fallback when there is no local install
   but there IS network egress to the IEDB service. This is what the validated
   muMAb 4D5 runs used (a ~180 s live call for the full 5-construct panel across
   7 alleles). `el` = eluted-ligand likelihood mode.
3. **Disclose-missing (degraded).** If neither is available, the module returns
   `immunogenicity_status = "skipped"` with a machine-readable
   `immunogenicity_reason`, and emits **no epitope numbers at all** (no
   approximate matrix, no zeros). Downstream, the scorecard and report **must
   disclose the missing axis** and rank on developability + humanness only.
   Fabricating a plausible-looking epitope count here would be worse than
   admitting the gap, so the module refuses to.

`reassess()` exposes this via `run_immunogenicity=True/False`; setting it False
forces the degraded path (useful for a fast developability-only pass, and for
the "no-predictor" acceptance case).

## Epitope-calling thresholds (validated)

Percentile-rank based, consistent across the local and IEDB tiers:

- **strong binder**: rank <= 2.0
- **binder**: rank <= 10.0
- **promiscuous**: binds (as a binder) across **>= 2 alleles** at the same core.

Promiscuous epitopes are the ones that matter most: a peptide that binds many
different HLA-DR alleles will be presented in a larger fraction of the human
population, so it is a broader immunogenicity risk than an allele-private binder.

## Reported quantities

Per construct, aggregated over the Fv (both chains):

- `Fv_epitope_load` — total binder count across the peptide scan (the headline
  "epitope load").
- `Fv_promiscuous` — count of promiscuous epitopes (>=2 alleles).
- `promisc_in_FR` / `promisc_in_CDR` — promiscuous epitopes split by region.
  **The framework promiscuous count is the actionable humanization target** —
  those are epitopes you can often remove by using a human framework, without
  touching the paratope. CDR epitopes are constrained by binding and usually
  cannot be freely mutated.

A per-chain breakdown (`immunogenicity_chain`) is also produced for the figures.

## The allele panel is a parameter

The default is the 7-allele IEDB reference HLA-DR set
(`DR_PANEL_7` in `ab_core.py`):

```
HLA-DRB1*01:01, HLA-DRB1*03:01, HLA-DRB1*04:01, HLA-DRB1*07:01,
HLA-DRB1*08:01, HLA-DRB1*11:01, HLA-DRB1*15:01
```

These are chosen for broad population coverage and are the IEDB-recommended
reference panel. **Swap them (via the `dr_panel` argument) when a project targets
a specific population or HLA background** — e.g. add DRB1*09:01 / DRB1*12:01 for
East-Asian coverage. Widening the panel raises absolute epitope counts, so only
compare constructs scanned against the *same* panel.

## Interpreting for a report

- Lead with `Fv_epitope_load` and `Fv_promiscuous`, comparing constructs.
- Emphasize the drop in **framework** promiscuous epitopes after humanization —
  that is the immunogenicity win the humanization is buying.
- If the axis was skipped, say so plainly, name the reason (no NetMHCIIpan /
  no IEDB egress), and state that ranking used developability + humanness only.
  Never present a zero or a guess as if it were a measured epitope load.

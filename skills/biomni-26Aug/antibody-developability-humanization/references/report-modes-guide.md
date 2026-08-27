# Report modes & the reference-present benchmark

The report builder (`scripts/build_report.py`) is **dual-mode** and
**branch-aware**. It produces a Phylo-branded PDF whose content adapts to (a)
whether a validated reference antibody was supplied, and (b) what kind of input
the antibody was. The orchestrator `scripts/run_pipeline.py` wires the whole
pipeline to it. This guide explains the modes so the report never over- or
under-claims.

## Two axes of adaptation

### Axis 1 — `mode` (is there a clinical/validated reference?)

Inferred automatically: `mode = "reference_present"` iff a `benchmark_out` is
supplied, else `"reference_absent"` (the default).

- **`reference_absent` (DEFAULT).** The normal use case: an arbitrary antibody
  with no known clinical counterpart. The report covers developability,
  immunogenicity, and (if non-human) humanization candidates. **It contains ZERO
  benchmark/validation language** — no mention of a reference, convergence,
  concordance, recovery, held-out, or blind design. This is enforced and was
  verified to leak none of those terms.
- **`reference_present` (OPTIONAL).** Used only when you deliberately supply a
  known reference (the muMAb 4D5 -> trastuzumab demo). Adds the benchmark section:
  whole-domain identity vs the reference, back-mutation concordance, and canonical
  recovery. This is a **convergence test of a blind design**, never a design
  input.

### Axis 2 — `branch` (what is the input?)

Set by the species/format gate (`scripts/species_format_gate.py`):

- **`paired_nonhuman`** — a non-human paired VH+VL. Full pipeline: humanize +
  compare acceptors, then assess all constructs. Methods §1.1 describes CDR
  **grafting**.
- **`paired_human`** — an already-human paired VH+VL (e.g. adalimumab). **No
  humanization is proposed**; the report is assess-only (developability +
  immunogenicity) and the executive summary states the framework identity
  *confirms the domains are already human*. Methods §1.1 describes CDR **liability
  annotation** (not grafting).
- **`single_domain`** — a single chain (VHH/nanobody or lone VH/VL). Assessed
  as-is, no pairing, no humanization.

The builder switches wording on `branch` (e.g. the CDR-use sentence is `"grafting"`
for non-human vs `"liability annotation"` for human), so the same code produces a
faithful report for each input type.

## The four validated report configurations

All four were rendered end-to-end and visually QA'd:

| Config | mode | branch | immuno | Pages | What it demonstrates |
|---|---|---|---|---|---|
| Reference-present | reference_present | paired_nonhuman | ok | 8 | Full flagship: humanization + benchmark |
| Reference-absent | reference_absent | paired_nonhuman | ok | 7 | Default: humanization, no benchmark language |
| No-predictor | reference_absent | paired_nonhuman | skipped | 7 | Immunogenicity axis disclosed as unavailable, no fabricated numbers |
| Assess-only | reference_absent | paired_human | ok | 4 | Already-human antibody, no humanization proposed |

## Honest degradation in the report

When the MHC-II predictor is unavailable (`immunogenicity_status == "skipped"`),
the report **states the axis is unavailable, names the reason (no NetMHCIIpan /
no IEDB egress), and shows no epitope counts** — no zeros, no approximations. The
scorecard then ranks on developability + humanness only. The epitope columns are
dropped from the master table (and `_df_to_tbl` filters headers/columns in
lockstep so nothing misaligns). Never let the report imply an epitope load was
measured when it was not.

## Lead-construct selection (important)

The "lead" is the humanized construct the report features. Selection policy:

- **reference_absent:** the lead defaults to the **most human back-mutated
  design** (highest mean framework humanness) — with no ground truth, "most
  human while retaining the affinity-preserving back-mutations" is the defensible
  default.
- **reference_present:** the lead is the back-mutated design with the **highest
  VH identity to the reference** — because with a validated answer in hand, the
  meaningful lead is the one that best reproduces it (the point of the convergence
  test). In the muMAb 4D5 demo this correctly selects `hu_consensus_bmut`
  (VH3 consensus, 90.8% VH identity to trastuzumab, 5/5 canonical recovery),
  **not** the most-germline `hu_nearest_bmut` (91.9% humanness but only 73.3%
  identity to trastuzumab and 3/5 recovery).

You can always override with an explicit `lead_key` if a project wants to feature
a specific construct. The benchmark's `graft_key` is derived from the lead
(same philosophy, `bmut`->`graft`) so the concordance table compares the lead
back-mutated design against its own naive graft.

## Benchmark section contents (reference-present only)

- **Whole-domain identity** of each design vs the reference (VH & VL, BLOSUM62
  global alignment).
- **Back-mutation concordance**: for the lead, which blind back-mutations match
  the reference's actual framework residue (`concordant`), which push past it
  (`over_correction`), and which disagree (`discordant`).
- **Canonical/Vernier recovery**: of the canonical-class positions the reference
  changed, how many the blind design also recovered (5/5 in the muMAb 4D5 demo:
  H71, H73, H78, H93, L66).

These numbers are validated ground truth for the muMAb 4D5 case and must not be
altered. The benchmark is descriptive-only: it scores a design that was built
without ever looking at the reference.

# Developability liability rules (sequence-based)

This is the exact, validated rule set implemented in `scripts/ab_core.py`
(`liability_scan`) and rolled up by `scripts/developability_scan.py`. It is a
**sequence-only** chemical-degradation and manufacturability screen — no
structure required. Every motif is position-annotated (Kabat/IMGT number),
tagged as CDR or framework (FR), and given a severity that is **up-weighted 1.6x
when it falls in a CDR** (because chemical modification of a paratope residue is
far more likely to hurt binding than the same motif in a framework).

## Why CDR-weighting

A deamidation or isomerization hotspot in framework is usually tolerable; the
same hotspot inside a CDR can abolish antigen binding or create lot-to-lot
heterogeneity. The scan therefore reports two numbers:

- `total_liabilities` — raw motif count (all regions, equal weight).
- `total_weighted_burden` — sum of severities with CDR motifs multiplied by
  `cdr_weight = 1.6`. **This weighted burden is the headline developability
  metric** used for ranking constructs and in the scorecard figure.

`CDR_liabilities` (count of motifs in CDRs) and `N_glyco_sites` are also
reported because N-glycosylation sequons are a special manufacturability flag.

## The validated motif set

| Motif | Pattern | Base severity | Notes |
|---|---|---|---|
| N-glycosylation sequon | `N-X-[S/T]`, X≠P | **3** | Span 3 residues. Any occurrence is flagged; sequons in CDRs are the highest-priority removal target because they add glycan heterogeneity right at the paratope. |
| Deamidation NG (high) | `NG` | **3** | Fastest-deamidating Asn context. |
| Deamidation N-x (moderate) | `NS`, `NT`, `NH`, `NN`, `NA`, `NG`… | 2 | Any Asn followed by a residue known to accelerate deamidation (the "moderate" tier; NG is broken out separately as high). |
| Isomerization DG (high) | `DG` | **3** | Fastest-isomerizing Asp context. |
| Isomerization D-x (moderate) | `DS`, `DT`, `DD`, `DH`… | 2 | Asp isomerization moderate tier. |
| Oxidation Met | `M` | 2 | Methionine oxidation (single residue). |
| Oxidation Trp | `W` | 1 | Tryptophan oxidation (single residue). |
| Cysteine count anomaly | `count(C) != 2` per domain | flag | Each variable domain should carry exactly 2 cysteines (the conserved intradomain disulfide). An odd count implies a free/unpaired thiol (aggregation, scrambling risk); an even count >2 implies an extra disulfide. |

Severities are the **validated set** — do not silently re-tune them. If a project
needs a different weighting scheme (e.g. a customer's internal severity table),
change it explicitly and note the deviation in the report, because it changes the
weighted-burden ranking.

## Biophysical profile (companion, non-motif)

`ab_core` also computes a lightweight biophysical profile per construct
(Kyte-Doolittle hydropathy `KD`, pI, net charge, CDR3 length, aromatic/charged
content). These are **descriptive context**, not liabilities: they help explain a
high burden (e.g. a very hydrophobic CDR-H3) but do not add to the weighted
score. Treat them as flags for follow-up, not pass/fail gates.

## What "good" looks like

There is no universal cutoff — burden is comparative. In the validated muMAb 4D5
case the murine parent carried weighted burden 41.8 with an N-glycosylation
sequon in the light chain; every humanized graft removed that sequon
(`N_glyco_sites` 1 -> 0) and lowered burden to ~32-37. **The right use of this
scan is (a) rank constructs against each other, and (b) enumerate specific,
addressable motifs** (with their Kabat positions) for a redesign round — not to
declare an antibody "developable" from an absolute threshold.

## Interpreting the output for a report

- Lead with the **weighted burden** and the **N-glyco sequon count**.
- Call out any CDR liabilities by position (these are the actionable ones).
- If humanizing, show that grafting removed framework liabilities and, ideally,
  did not introduce new CDR liabilities (CDRs are grafted verbatim, so CDR
  liabilities are inherited from the parent — flag them as "carried over from the
  donor, candidates for targeted mutation").

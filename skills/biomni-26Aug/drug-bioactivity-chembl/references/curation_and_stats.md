# Curation, aggregation & selectivity — the analysis contract

This defines the "Standard" curation policy and the statistics the skill
reports. Applies to any compound; nothing here is compound-specific.

---

## 1. Why median + range (not mean)

ChEMBL affinities are pooled from many labs, assay formats, and years. They are
approximately **log-normal** and contain outliers from low-stringency assays.
Therefore:
- **Headline = median + full min–max range** per target (robust, what the user
  asked for).
- Add **IQR (25–75th percentile)** and **geometric mean** for robustness.
- Plot on a **log scale**; compute `pX = −log10(value_in_M)` for symmetric
  distributions. (ChEMBL's `pchembl_value` is the same idea, precomputed.)
- Report **n** and **# independent studies** (`document_chembl_id` nunique) so
  the reader can judge support. Flag **n = 1** targets as provisional.

---

## 2. The "Standard" quality filter (documented, reproducible)

Applied to **protein-target** records (primary + off-target tiers) for the
aggregation set:

| Step | Rule | Action |
|------|------|--------|
| 1 | `data_validity_comment == 'Potential transcription error'` | **drop** |
| 2 | `standard_units == 'nM'` | keep for aggregation; non-nM counted & dropped |
| 3 | `standard_relation == '='` | exact → aggregate |
| 4 | `standard_relation in (>, <, >=, <=)` | **set aside**, report as bounds |
| 5 | `data_validity_comment == 'Outside typical range'` | **keep**, but flag |

**Provenance must reconcile:**
```
raw_pulled = clean_exact_nM + transcription_error + non_nM_units + censored
```
Emit this as a provenance table in the report Methods. If it does not reconcile,
stop and debug — do not report aggregates from an unbalanced ledger.

(`standard_filter()` returns exactly these buckets; the worked-example olaparib
run went 1108 raw → 256 protein biochemical → 230 clean exact-nM after removing
1 transcription error + 19 non-nM + 6 censored.)

---

## 3. Assay classification (separate molecular from cellular)

Never mix isolated-enzyme potency with whole-cell activity. Classify each record:

- **biochemical** — recombinant-enzyme inhibition or direct binding
  (`assay_type` B/F on a single protein). This is the potency/selectivity set.
- **cellular_target_engagement** — target readout in intact cells
  (e.g. cellular PAR levels). Reported separately.
- **antiproliferation** — cell growth/viability (GI50/CC50/IC50 on a cell line).
  Reported separately as a secondary section.

Guard: an assay whose description says "enzyme **expressed in** Sf9 / E. coli /
baculovirus / HEK" is still **biochemical**, even though it mentions cells.

Cellular readouts are routinely ~100–1000× higher (weaker) than biochemical
IC50s — that gap is expected biology (permeability, ATP/NAD competition), not an
error, and is worth noting in the report.

---

## 4. Aggregation output (per target × measurement type)

Columns: `n, median, q25, q75, vmin, vmax, geomean, n_studies`, plus retained
assay context. Kd(app)/Ki(app) are folded into Kd/Ki with an `apparent` flag.
Report each measurement type (IC50, Ki, Kd) as its own row — do not average
across types (they measure different things).

---

## 5. Selectivity

- **Reference** = the primary target's median (biochemical, exact-nM). If there
  are two primaries (e.g. a dual inhibitor), report fold vs each and also the
  **primary-vs-primary ratio**.
- **fold-selectivity** of an off-target = `median(off-target) / median(primary)`.
  Higher = more selective (drug is that many-fold weaker on the off-target).
- **Censored off-targets** → lower bounds ("≥ Nx"): if the off-target IC50 is
  `> 10000 nM`, its selectivity is `≥ 10000/primary_median`.
- Flag **n = 1** off-targets as provisional; a single datapoint is not a robust
  selectivity claim.

---

## 6. Choosing the primary target(s) — HUMAN REVIEW REQUIRED

`detect_primary_targets` ranks single-protein targets by a support-weighted
score `median_nM / sqrt(n)` (requires n ≥ 3) so a lone ultra-potent outlier
cannot outrank a well-measured true target. **This is a starting point, not
ground truth.** Always:

1. Print `primary_candidate_table(protein)` and inspect the top rows.
2. Cross-check against the drug's known mechanism of action / literature
   (via `LiteratureSearch`). ChEMBL frequently **splits one biological target
   across several target records** (e.g. imatinib's ABL appears as "Tyrosine-
   protein kinase ABL1", "ABL2", "Bcr/Abl fusion protein"), which can scramble
   pure-median ranking.
3. If the auto-pick disagrees with the known primary target(s), **override** via
   `tier_targets(df, primary_target_ids=[...])`. For a multi-target drug, it is
   legitimate to designate several primaries and report the profile across them.

Document in Methods whether the primary target(s) were auto-detected or set
manually, and on what basis.

---

## 7. Sanity check before reporting

Compare the primary median against literature-expected potency (`sanity_flag`,
and a `LiteratureSearch` for the discovery/potency paper). If the median is wildly
off (orders of magnitude), suspect a units error or a wrong primary-target
assignment — investigate, don't publish the number. (Olaparib PARP1 landed at
~5 nM median, consistent with the ~1–6 nM literature range → PASS.)

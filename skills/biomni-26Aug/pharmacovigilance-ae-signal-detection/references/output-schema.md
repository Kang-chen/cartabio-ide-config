# Output Schema

Column and file definitions for everything the pipeline writes to `out_dir`.

## `disproportionality_full.csv` (one row per drug × event)

| Column | Source | Definition |
|--------|--------|------------|
| `drug` | input | Resolved drug name (or the pooled pseudo-drug label, default `"combined"`) |
| `event` | input | MedDRA Preferred Term (adverse event), as stored in FAERS (upper-case) |
| `a` | compute | Reports mentioning **both** drug and event |
| `b` | compute | Reports with the drug but not this event (drug_total − a) |
| `c` | compute | Reports with the event but not this drug (event_total − a) |
| `d` | compute | Reports with neither (N − a − b − c) |
| `ror` | compute | Reporting Odds Ratio = (a·d)/(b·c) |
| `ror_lower` | compute | ROR 95% CI lower bound |
| `ror_upper` | compute | ROR 95% CI upper bound |
| `prr` | compute | Proportional Reporting Ratio |
| `chi2` | compute | Chi-square (Yates-corrected) |
| `p_value` | compute | Chi-square p-value |
| `fdr` | compute | Benjamini-Hochberg FDR q-value (per-drug family; NaN if `use_fdr=False` or statsmodels unavailable) |
| `signal` | compute | Boolean — passes the full `SignalCriteria` rule |
| `label_status` | annotate | `boxed` / `labeled` / `unlabeled` / `unknown` (vs. the drug's FDA SPL) |
| `noise_reason` | annotate | Why the term is a reporting artifact, or empty |
| `is_noise` | annotate | Boolean — term is a non-clinical/administrative/indication artifact |
| `category` | annotate | Coarse clinical category (Infection, Thrombosis/Vascular, Cardiac, Malignancy, Hepatobiliary, Haematologic, Renal, Metabolic/Lipid, GI/Perforation, Skin/Hypersensitivity, Musculoskeletal, Immune/Autoimmune, Neuro/Psych, Other) |
| `soc` | annotate | MedDRA System Organ Class (curated fallback map) |
| `low_count` | annotate | Boolean — signal with `a < min_cases_confident` (fragile count) |
| `extreme_ror` | annotate | Boolean — signal whose ROR is an implausible high outlier (per-drug Tukey fence on ln ROR, with absolute floor) |
| `low_confidence` | annotate | Boolean — `low_count OR extreme_ror`; signal retained but flagged fragile/inflated |
| `low_confidence_reason` | annotate | Short reason(s): `a<N`, `ROR outlier (>T)`, or both; empty otherwise |
| `lit_support` | annotate* | Boolean — event appears in attached literature (only present if `attach_literature` was run) |

\* `lit_support` is added only when the agent runs `LiteratureSearch` and calls
`attach_literature`; otherwise the column is absent.

### `label_status` semantics
- `boxed` — event text found in the label's **boxed warning**
- `labeled` — found in adverse-reactions / warnings-and-cautions / warnings sections
- `unlabeled` — **not** found in that label (⚠️ means "absent from this label", not "proven novel")
- `unknown` — no label could be retrieved for the drug

Matching (`match_label`) is a token/phrase match: the full phrase in the label
text, OR at least `max(2, 60% of the term's content tokens)` present; a
single-token term must appear as that exact token. Stopwords are dropped before
matching.

### `is_noise` categories (`flag_noise`)
- **product/quality** — DRUG INEFFECTIVE, OFF LABEL USE, PRODUCT QUALITY ISSUE, injection-site administration terms, etc.
- **indication/disease** — the treated disease or indication marker, incl. specific indications reported as PTs (rheumatoid arthritis, ulcerative colitis / colitis ulcerative, psoriasis, Crohn's, atopic dermatitis); reflects the population, not toxicity (confounding by indication)
- **procedure** — surgical/interventional PTs (`-ectomy`/`-ostomy`/`-otomy`/`-oscopy`/`-plasty`, surgery, transplant, biopsy, catheter, dialysis, transfusion, etc.); reflect surgical history or an intervention, not a direct ADR
- **nonspecific** — DEATH, condition, general symptom, and other terms with no organ-level information
- **lab-marker** — isolated laboratory/serology markers (e.g. anti-CCP antibody positive)

Top-signal tables filter `is_noise == True`; the full CSV keeps everything.

## Summary tables

| File | Contents |
|------|----------|
| `table1_overview.csv` | Study parameters + the **single-source-of-truth** count breakdown: terms tested, **signals passing criteria**, — non-clinical artifacts excluded, **genuine ADR signals**, — labeled / unlabeled / unknown, — low-confidence (flagged, retained), signal-criteria string, low-confidence rule string |
| `table2_top_signals.csv` | Top 20 genuine ADR signals (noise-filtered) for the primary subject: event, cases, ROR (95% CI), PRR, χ², label status, **`Conf.`** (`✓` robust / `low` = low-confidence, retained not removed) |
| `table3_unlabeled_signals.csv` | Top 15 **unlabeled** signals: event, cases, ROR (95% CI), category, SOC (omitted if none) |

### Single source of truth for signal counts (`run_analysis.signal_counts`)
All signal counts printed anywhere in the report/tables/figures come from one
helper so they cannot disagree. It guarantees, by construction, for the primary
subject:
```
n_pass_criteria == n_genuine + n_noise           # e.g. 68 = 57 + 11
n_genuine       == n_labeled + n_unlabeled + n_unknown   # e.g. 57 = 21 + 36 + 0
```
`n_pass_criteria` = rows with `signal=True` (statistical criteria only);
`n_noise` = `signal & is_noise`; `n_genuine` = `signal & ~is_noise` (**the
headline count**); label split over the genuine set; `n_low_confidence` =
`genuine & low_confidence`. The previous bug printed `n_pass_criteria` (noise-
included, 68) next to the noise-excluded label split (21+36=57) in the same
sentence; deriving everything from `signal_counts` removes that inconsistency.

## Figures (`figures/`, PNG + SVG)

| Name | File stem | Content |
|------|-----------|---------|
| bar | `fig1_top_signals_bar` | Top signals by ROR, bars colored by label status |
| volcano | `fig2_volcano` | log2(ROR) x −log10(FDR q); top events as numbered diamonds on a side ladder |
| forest | `fig3_forest` | ROR point estimate + 95% CI whiskers for top signals |
| heatmap | `fig4_soc_heatmap` | drug × event ROR heatmap (diverging, centered at ROR = 1); multi-drug only |
| summary | `fig5_summary_panel` | 4-panel: signal counts, labeled/unlabeled split, category mix, SOC distribution |

## Report

`report_<subject>.pdf` — Phylo-branded PDF assembled by `build_report`:
optional infographic (page 1), executive summary, methods, results (with
figures + tables), limitations, conclusions, references (if attached), and
suggested next steps. Validate with `validate_pdf(path)` → `{ok, pages, bytes,
has_text, issues}`.

## In-memory result (`run_analysis` return dict)

| Key | Value |
|-----|-------|
| `results` | full annotated DataFrame (schema above) |
| `counts` | dict: `drug_totals`, `event_totals`, `cooccur`, `n_total`, `events`, `per_drug_events` |
| `tables` | dict of written CSV paths |
| `figures` | dict `{figure_name: png_path}` |
| `context` | `ReportContext` for `finalize_report` |
| `report` | draft PDF path |
| `drugs` | resolved drug list (0-report drugs dropped) |
| `mode` | detected/forced mode |
| `dropped` | list of `(drug, reason)` dropped during resolution/validation |
| `literature_query` | pre-built `LiteratureSearch` query string |
| `infographic_prompt` | pre-built `GenerateImage` prompt string |

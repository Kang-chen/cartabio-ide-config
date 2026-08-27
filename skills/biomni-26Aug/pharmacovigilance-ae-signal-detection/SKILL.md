---
id: "skill_fb055d3413196f5e948f5159c1207321"
name: "pharmacovigilance-ae-signal-detection"
description: "Use to detect post-market adverse-event signals for a drug, class, or target from FDA FAERS/OpenFDA. Computes ROR, PRR, chi-square, and FDR disproportionality, maps MedDRA classes, filters reporting artifacts, and compares signals with FDA label warnings; triggers on pharmacovigilance, side effects, or labeled-vs-unlabeled events."
category: "drug_discovery"
visibility: "public"
starting-prompt: "Run a FAERS adverse-event signal-detection scan for upadacitinib"
---

# Pharmacovigilance Adverse-Event Signal Detection

## When to Use This Skill

- **Post-market safety scan** for a single drug (generic or brand): "What adverse-event signals show up for semaglutide in FAERS?"
- **Class-wide signal detection**: "Compare safety signals across SGLT2 inhibitors" / "anti-TNF biologics"
- **Target-anchored safety review**: "What safety signals do JAK1 inhibitors share?" — resolves target → drugs via Open Targets, then pools them
- **Labeled vs. unlabeled triage**: separate expected on-label adverse events from potentially novel (unlabeled) signals worth follow-up
- **Disproportionality analysis**: compute ROR / PRR / chi-square / FDR against the whole FAERS background or a custom active comparator

**Do NOT use for:**
- Causal risk or incidence estimates — FAERS is a spontaneous-reporting system with no denominator; disproportionality measures **differential reporting**, not risk. (See Interpretation Guidelines.)
- Controlled-trial safety analysis (use a clinical-trial or survival-analysis skill)
- Individual case narrative review or regulatory case processing
- Efficacy comparisons (use `literature-review` / `clinicaltrials-landscape`)

---

## Installation

| Software | Version | License | Commercial Use | Installation |
|----------|---------|---------|----------------|--------------|
| pandas | ≥1.3 | BSD-3 | ✅ Permitted | `pip install pandas` |
| numpy | ≥1.20 | BSD-3 | ✅ Permitted | `pip install numpy` |
| scipy | ≥1.7 | BSD-3 | ✅ Permitted | `pip install scipy` |
| statsmodels | ≥0.13 | BSD-3 | ✅ Permitted | `pip install statsmodels` |
| requests | ≥2.25 | Apache-2.0 | ✅ Permitted | `pip install requests` |
| matplotlib | ≥3.4 | PSF | ✅ Permitted | `pip install matplotlib` |
| reportlab | ≥3.6 | BSD | ✅ Permitted | `pip install reportlab` |
| pypdf | ≥3.0 | BSD-3 | ✅ Permitted | `pip install pypdf` |

```bash
pip install pandas numpy scipy statsmodels requests matplotlib reportlab pypdf
```

**System requirements:** Internet connection for the OpenFDA API (`api.fda.gov`) and, for target mode, the Open Targets GraphQL API. No API key is required, but an optional free OpenFDA key raises rate limits (pass `api_key=`).

---

## Inputs

**Required:**
- **`query`** — one of:
  - a **drug name** (str): `"semaglutide"`, `"Humira"` (explicit mode)
  - a **list of drug names**: `["upadacitinib", "tofacitinib"]` (explicit mode, pooled)
  - a **drug class** (str): `"SGLT2 inhibitors"`, `"anti-TNF"`, `"statins"` (class mode)
  - a **molecular target symbol** (str): `"JAK1"`, `"EGFR"` (target mode)

**Optional:**
- **`mode`** — force `"explicit"` | `"class"` | `"target"` (default: auto-detect via `detect_mode`)
- **`comparator`** — custom background drug list for an active-comparator analysis (default: whole FAERS)
- **`criteria`** — `SignalCriteria(...)` to change signal thresholds (defaults below)
- **`api_key`** — OpenFDA API key for higher rate limits
- **`top_n_events`** — reaction terms pulled per drug (default 500, the OpenFDA facet cap)

---

## Outputs

Written under `out_dir`:

**Report:**
- `report_<subject>.pdf` — Phylo-branded PDF: infographic (optional) + executive summary, methods, results, figures, tables, limitations, conclusions, references, next steps

**Figures (PNG + SVG, 120 DPI):**
- `figures/fig1_top_signals_bar` — top signals by ROR, colored by label status
- `figures/fig2_volcano` — log2(ROR) vs. −log10(FDR q), diamonds on a labeled ladder
- `figures/fig3_forest` — ROR point estimates with 95% CI whiskers
- `figures/fig4_soc_heatmap` — drug × event ROR heatmap (multi-drug/class only)
- `figures/fig5_summary_panel` — 4-panel overview (signal counts, label split, category mix, SOC)

**Tables (CSV):**
- `disproportionality_full.csv` — every (drug, event) with a,b,c,d, ROR (+CI), PRR, chi2, p, FDR q, signal flag, label status, noise flag, category, SOC, **low_count / extreme_ror / low_confidence / low_confidence_reason**
- `table1_overview.csv` — study parameters + the **single-source-of-truth** count breakdown (signals passing criteria = genuine + artifacts; genuine = labeled + unlabeled + unknown; plus low-confidence count)
- `table2_top_signals.csv` — top genuine ADR signals (noise-filtered), with a **`Conf.`** column marking low-confidence rows (retained, not removed)
- `table3_unlabeled_signals.csv` — potentially unlabeled signals shortlist

---

## Clarification Questions

Ask before running if unclear:

1. **Subject type** — is the target a single drug, a class, or a molecular target? (auto-detected, but confirm for ambiguous strings like an all-caps drug code)
2. **Comparator** — whole FAERS background (standard, default) or a specific active-comparator drug set?
3. **Signal thresholds** — standard rule (ROR CI lower > 1, PRR ≥ 2, χ² ≥ 4, ≥ 3 cases, FDR q < 0.05) or custom?
4. **Depth** — full PDF report with figures + literature grounding + infographic, or just the signal table?

---

## Standard Workflow

🚨 **USE THE SCRIPTS. Do not re-implement disproportionality math, the OpenFDA query rules, or the figure/report styling inline — they encode hard-won API and statistical correctness.** 🚨

The pipeline is orchestrated by `run_analysis.py`. Two steps need **agent tools** (`LiteratureSearch`, `GenerateImage`) that a plain Python module cannot call — the orchestrator hands you the exact query/prompt strings and you feed the results back.

**Step 1 — Run the deterministic pipeline (resolve → query → compute → annotate → figures → tables → draft report):**
```python
import sys; sys.path.insert(0, ".")
from scripts.run_analysis import run_analysis, AnalysisConfig

cfg = AnalysisConfig(
    query="upadacitinib",                 # str | list | class | target
    out_dir="pv_results/upadacitinib",
    # mode="target",                       # optional: force a mode
    # comparator=["tofacitinib", "baricitinib"],   # optional active comparator
)
result = run_analysis(cfg)
```
**✅ VERIFICATION:** prints `[1/6] Resolving ...` through `[6/6] Building PDF report ...` and a final report path with page count. `result["results"]` is the full annotated DataFrame; `result["report"]` is the draft PDF path.

**Step 2 — Ground the top signals in the literature (agent tool `LiteratureSearch`):**
```python
q = result["literature_query"]     # pre-built query string from the orchestrator
print(q)
```
Now **call the `LiteratureSearch` tool** with that query. Collect the returned records into a list of dicts with at least `title`, `authors`, `year`, `journal` (and `doi`/`url` if present).

**Step 3 — Generate the mechanism/summary infographic (agent tool `GenerateImage`):**
```python
print(result["infographic_prompt"])   # pre-built schematic prompt
```
**Call the `GenerateImage` tool** with that prompt; save the PNG to `out_dir` (e.g. `pv_results/upadacitinib/infographic.png`).

**Step 4 — Rebuild the final report with references + infographic:**
```python
from scripts.run_analysis import finalize_report

final_pdf = finalize_report(
    result["context"], cfg.out_dir,
    references=lit_records,                 # from Step 2
    infographic_path="pv_results/upadacitinib/infographic.png",  # from Step 3
)
print("Final report:", final_pdf)
```
**✅ VERIFICATION:** `validate_pdf(final_pdf)["ok"] is True` (≥ 2 pages, extractable text). The report now includes the References section and the infographic on page 1.

> **Skip Steps 2–4** if the user only wants the signal table/figures — `run_analysis` already produced a complete report without literature/infographic. `finalize_report` and the two agent tools are purely additive.

---

## ⚠️ CRITICAL — DO NOT:

- ❌ **Manually URL-encode search terms then wrap them in quotes** → double-encodes spaces inside quoted phrases and silently returns 0 hits. **The scripts handle encoding; pass raw terms.**
- ❌ **Resolve a drug class from the `/drug/event.json` endpoint** → co-reported drugs (metformin, aspirin) contaminate results. **`resolve_class_members` uses `/drug/label.json` — do not change it.**
- ❌ **Treat ROR/PRR as risk or causal effect** → they measure differential *reporting*. State this in every deliverable.
- ❌ **Derive event totals by summing `a` across your drug set** unless the set partitions all of FAERS → pass whole-universe event totals (the orchestrator does this via a background facet + single-term fallback).
- ❌ **Report DRUG INEFFECTIVE / OFF LABEL USE / DEATH / PRODUCT QUALITY ISSUE as safety signals** → these are administrative/non-clinical terms. **`flag_noise` marks them; top-signal tables must filter `is_noise`.**
- ❌ **Mix the noise-included signal total with noise-excluded breakdowns in one statement** (e.g. "68 signals (21 labeled + 36 unlabeled)" where 21+36=57) → always pull counts from **`run_analysis.signal_counts`**, which guarantees `pass_criteria = genuine + artifacts` and `genuine = labeled + unlabeled + unknown`. Report the genuine count as the headline.
- ❌ **Treat an extreme ROR as a strong signal without checking `low_confidence`** → very large RORs are often notoriety/stimulated-reporting or mechanism/efficacy-adjacent artifacts. **`flag_low_confidence` marks them; surface the flag, do not silently drop or over-interpret.**
- ❌ **Write ReportLab PDFs or HDF-like files directly onto `/mnt`** (S3 FUSE has no random-access writes) → build in `/workspace` then `cp`. **`build_report` already does this.**
- ❌ **Use `sed -i` or `cat >>` append on files under `/mnt`** → S3 FUSE returns "Function not implemented" / silently no-ops. Use the `Write`/`Edit` tools.
- ❌ **Put Unicode superscripts/subscripts in the PDF** → use HTML entities (`&#967;`=χ, `&#178;`=², `&#177;`=±). The report helpers already do.

---

## ⚠️ IF SCRIPTS FAIL — Failure Hierarchy:

1. **Fix and Retry (90%)** — install a missing package, re-run (OpenFDA 429 → the client already backs off; just retry).
2. **Modify Script (5%)** — edit the script, document the change.
3. **Use as Reference (4%)** — read the script, adapt, cite it.
4. **Write from Scratch (1%)** — only if genuinely impossible; explain why.

**Never skip to inline disproportionality/query code without trying the scripts.**

---

## Common Issues

| Error / Symptom | Cause | Solution |
|-----------------|-------|----------|
| **0 drugs resolved (class mode)** | Informal class name not in OpenFDA EPC vocabulary | Add/point to canonical label via `_CLASS_SYNONYMS`; or pass drugs explicitly |
| **0 drugs resolved (target mode)** | Symbol not found in Open Targets, or no approved/clinical drugs | Verify the gene symbol; try a class or explicit list |
| **A resolved drug has 0 FAERS reports** | Not yet marketed / no spontaneous reports | The orchestrator drops 0-report drugs automatically (see `result["dropped"]`) |
| **HTTP 429** | OpenFDA rate limit | Client retries with backoff; pass `api_key=` for higher limits |
| **HTTP 400 on a term** | Special characters (apostrophes) in the MedDRA term | `count_single_term` retries apostrophe variants automatically |
| **All-NaN heatmap column** | A drug shares no tested events with others | `fig_soc_heatmap` drops all-NaN columns automatically |
| **Empty top-signal table** | Every top-ROR term was a noise term | Expected for some drugs; inspect `disproportionality_full.csv` |
| **PDF is 0 bytes on /mnt** | Wrote ReportLab directly to S3 FUSE | Use `build_report` (builds in /workspace then copies) |

---

## Interpretation Guidelines

- **Disproportionality ≠ risk.** ROR/PRR/χ² quantify whether an event is reported *more often than expected* for a drug relative to the background — a hypothesis-generating signal, not incidence, causation, or absolute risk. Confounding by indication, notoriety/stimulated reporting, and channelling all inflate ROR.
- **No denominator.** FAERS has no exposure denominator, so rates cannot be computed. Counts are reports, not patients or events.
- **Label grounding is heuristic.** `label_status` (boxed / labeled / unlabeled) is a text match against the OpenFDA structured product label of a representative member; wording mismatches can misclassify. "Unlabeled" means *not found in that label*, not *proven novel*.
- **SOC is a curated fallback map**, not the licensed MedDRA hierarchy — a coarse grouping for readability.
- **Noise filtering is deliberately conservative but broad on artifacts.** Administrative/product-quality terms (DRUG INEFFECTIVE, OFF LABEL USE, product quality), **surgical/interventional procedures** (colectomy, ileostomy, colonoscopy — reflect surgical history, not drug toxicity), **the treated indication itself** (rheumatoid arthritis, ulcerative colitis, psoriasis — confounding by indication), nonspecific terms (DEATH), and isolated lab markers are flagged (`is_noise`) and excluded from top-signal tables but retained in the full CSV. This matters: without it, the treated disease and its procedures dominate the top ROR list.
- **Standard signal rule** (Evans-style, EMA-aligned): ROR 95% CI lower bound > 1, PRR ≥ 2, χ² ≥ 4, ≥ 3 cases, plus BH-FDR q < 0.05 to control multiplicity. Small-count signals (a < ~10) are fragile even when they pass.
- **Low-confidence flag (marks, never removes).** Every signal is additionally checked for fragility/inflation via `low_confidence` = `low_count` (a < `min_cases_confident`, default 10) **OR** `extreme_ror` (ROR above a per-drug Tukey far-out fence on ln ROR, with an absolute floor). These rows stay in all outputs but are marked (a `Conf.` column, hatched bars, forest diamonds/rings, volcano rings) and named in the report. **An implausibly large ROR is usually notoriety/stimulated reporting or a mechanism/efficacy-adjacent term, not a stable safety effect** — treat flagged rows as hypothesis-fragile. Note: because the OpenFDA facet returns only the top ~500 most-frequent reactions, the small-count trigger mainly matters for *rarely reported* drugs; for high-volume drugs the dominant trigger is extreme ROR.
- **Signal counts are derived from a single source of truth** (`run_analysis.signal_counts`): `signals passing criteria = genuine + non-clinical artifacts`, and `genuine = labeled + unlabeled + unknown`. The **headline count is the genuine (noise-excluded) number**; the artifacts-excluded count is always shown alongside. Never mix the noise-included total with noise-excluded breakdowns in the same statement.
- **Pooled (class/target) rows** use a representative member's label for grounding; per-drug rows use each drug's own label.

---

## Suggested Next Steps

1. **Deep-dive an unlabeled signal** — use `literature-review` to check whether the top unlabeled events have published mechanistic or epidemiologic support.
2. **Map the competitive/clinical context** — use `clinicaltrials-landscape` for the drug class's trial landscape.
3. **Target biology** — use `open-targets` to explore the target's genetic/mechanistic associations behind a class effect.
4. **Active-comparator refinement** — re-run with `comparator=[...]` to reduce indication confounding against a same-class or same-indication set.

---

## Related Skills

- `open-targets` — target → drug resolution and target biology (used internally for target mode)
- `clinicaltrials-landscape` — trial-level landscape for a drug class
- `literature-review` — evidence grounding for individual signals
- `pdf-report-generation` — general Phylo-branded PDF patterns

---

## References

- OpenFDA drug API: https://open.fda.gov/apis/drug/
- FAERS: https://www.fda.gov/drugs/surveillance/questions-and-answers-fdas-adverse-event-reporting-system-faers
- Open Targets Platform API: https://platform.opentargets.org/api
- Evans SJW et al. *Pharmacoepidemiol Drug Saf* 2001 (PRR method); van Puijenbroek EP et al. 2002 (ROR).
- See `references/api-parameters.md` for OpenFDA/Open Targets query rules and gotchas
- See `references/methodology.md` for the disproportionality math and signal criteria
- See `references/output-schema.md` for full column/output definitions
- See `references/config-pattern.md` for `AnalysisConfig` / `SignalCriteria` usage and the agent-tool integration pattern

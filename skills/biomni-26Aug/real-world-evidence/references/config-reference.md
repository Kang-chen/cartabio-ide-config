# Config reference — `00_config_template.R`

Every parameter that adapts this pipeline to a new study lives in one `CFG` list.
Copy the template, edit it, and `source()` it before running scripts `01`–`05`.
The analysis scripts must never hardcode disease-, drug-, or dataset-specific
values — if you find yourself wanting to, add a config field instead.

---

## 1. Input tables (`CFG$paths`)

| Field | Required | Contents |
|---|---|---|
| `patients` | yes | One row per patient: id, sex, age/DOB, date-of-death (optional) |
| `encounters` | yes | One row per admission: ids, admit/discharge times, in-hospital death flag/time |
| `diagnoses` | yes | Long format: subject_id, hadm_id, `icd_code`, `icd_version` |
| `drugs` | for treatment profiling | Long: subject_id, hadm_id, `drug`, `route`, `starttime` |
| `icu_stays` | optional | subject_id, hadm_id, stay_id, `intime`, `outtime`, `los` |
| `out_dir` | yes | Base output dir (`/mnt/results`). `tables/`, `figures/` created under it |

Accepts CSV or parquet (detected by extension). Set any optional table to `NA`.

**Source-agnostic.** MIMIC-IV (demo or full), OMOP CDM, claims extracts, and
registry pulls all map onto this contract — you only change the column mapping
in section 2. See `rwe-methodology.md` for OMOP/claims mapping notes.

> ⚠️ **Data-source licensing.** Not all sources that fit this schema carry the
> same license. The MIMIC-IV **demo** (100 patients) is open-access (ODbL
> v1.0, no credentialing). The **full MIMIC-IV** is **credentialed,
> restricted-access** under the PhysioNet Credentialed Health Data Use Agreement
> 1.5.0 — access requires PhysioNet credentialing, a signed DUA, and CITI
> training, and the DUA **prohibits redistribution and sharing with third
> parties** (including via APIs or online platforms). **Never use the full
> MIMIC-IV from an unauthorized mirror** (e.g. a HuggingFace dataset mirror);
> that does not constitute lawful access under the DUA. If the intended source
> is unreachable from the sandbox, report the network failure and either skip
> the run or use a small synthetic fixture matching the canonical schema above.
> Other credentialed registries (eICU, etc.) carry analogous restrictions —
> verify the license before use.

## 2. Column mapping (`CFG$cols`)

Named vectors mapping **canonical name = "your column name"**. If your table
already uses the canonical name, leave it. Canonical names used downstream:

- patients: `subject_id`, `sex`, `dob`, `dod`
- encounters: `subject_id`, `hadm_id`, `admittime`, `dischtime`, `deathtime`, `expire_flag`
- diagnoses: `subject_id`, `hadm_id`, `icd_code`, `icd_version`
- drugs: `subject_id`, `hadm_id`, `drug`, `route`, `starttime`
- icu_stays: `subject_id`, `hadm_id`, `stay_id`, `intime`, `outtime`, `los`

`age_is_precomputed` — `TRUE` if the `dob` column already holds age in years
(MIMIC `anchor_age`); `FALSE` if it holds a birth date to difference against the
index date.

## 3. Cohort / eligibility definition (`CFG$cohort_codes`, `CFG$severity_tiers`)

`cohort_codes` is a list keyed by ICD version string. Each entry has `prefix`
(matched with `startsWith`) and `exact` (matched with `%in%`). Codes are
upper-cased and trimmed first. A patient qualifies if **any** of their diagnosis
codes match **any** rule.

**What these codes mean depends on `CFG$comparator` (§5):**

- With `"rest_of_population"` / `"rest_of_icu"`, `cohort_codes` **define the
  cohort**; the comparator is everyone else (or the rest of the ICU).
- With `"active_comparator"`, `cohort_codes` define **eligibility** — the
  disease pool (e.g. *all* type-2 diabetes: ICD-10 `E11` **and** ICD-9 `250`
  together). The two treatment **arms** are then defined by **drug exposure**
  (§6b), not by these codes.

> ⚠️ **Do not split treatment arms by ICD-code vintage.** Using ICD-10 `E11`
> for one arm and ICD-9 `250` for the other (same disease) is incoherent —
> coding version reflects the encounter's calendar era / site, not which drug a
> patient received. For a drug-vs-drug contrast, put **all** the disease codes
> in `cohort_codes` (eligibility) and define arms by exposure (§6b) with
> `comparator = "active_comparator"`.

```r
cohort_codes = list(
  "9"  = list(prefix = "038", exact = c("99591","99592","78552")),
  "10" = list(prefix = c("A40","A41"), exact = c("R6520","R6521"))
)
```

`severity_tiers` (optional, most-severe-wins): ordered list from most to least
severe. Each tier has a `name` and per-version `codes` (same prefix/exact shape).
The first tier a patient matches is their severity label. Set `NULL` to skip.

**Caveat:** code-based phenotypes are imperfect (coding practices, version
drift). State the exact code set in the report Methods. See `rwe-methodology.md`.

## 4. Index date & time origin

- `index_rule`: `"first_qualifying_encounter"` — each patient's earliest
  admission carrying a qualifying code becomes the index encounter.
- `time_origin`: survival time-zero.
  - `"index_admit"` — index admission `admittime`.
  - `"index_icu_in"` — first ICU `intime` within the index encounter (needs
    `icu_stays`; falls back to admit time if absent).
- `require_icu`: `TRUE` keeps only cohort patients whose index encounter has an
  ICU stay. Use only when clinically intended (it changes the denominator).

**Immortal-time caution:** the time origin must not postdate the exposure that
defines the group. See `survival-guardrails.md`.

## 5. Comparator (`CFG$comparator`)

- `"rest_of_population"` — every patient not meeting the cohort definition.
- `"rest_of_icu"` — every ICU patient not in the cohort (needs `icu_stays`).
- `"active_comparator"` — **exposure-based arms for comparative drug studies**
  (see §6b). `cohort_codes` become eligibility; the two arms are defined by drug
  exposure. This is the correct design for "drug A vs drug B" questions.

For the code-based comparators, comparator patients get an index encounter =
their first (ICU) admission. For formal matched comparisons, build the matched
set upstream and pass it as the cohort/comparator tables (matching is out of
scope for the default pipeline).

### 6b. Exposure-based arms (active comparator)

When `comparator = "active_comparator"`, two additional maps split the eligible
pool into arms by **drug exposure**, using the same first-hit-wins substring
matching as `treatment_map`:

```r
comparator              = "active_comparator",
cohort_codes            = list(   # ELIGIBILITY: all type-2 diabetes
  "9"  = list(prefix = "250"),
  "10" = list(prefix = "E11")),
exposure_cohort_map     = list(`GLP-1 RA` = c("semaglutide","dulaglutide","liraglutide","exenatide")),
exposure_comparator_map = list(`DPP-4i`   = c("sitagliptin","linagliptin","saxagliptin","alogliptin")),
exposure_overlap_rule   = "first_exposure"   # or "exclude"
```

- **Eligibility:** patients matching `cohort_codes`.
- **Arms:** within the eligible pool, the cohort arm = patients whose
  (route-filtered) drug orders match `exposure_cohort_map`; the comparator arm =
  those matching `exposure_comparator_map`. Route / `drug_exclude` filters
  (§6) are applied before matching, so arm exposure and profiled exposure are
  defined consistently. Keep the two maps mutually exclusive at the drug level.
- **New-user time zero:** the index encounter (and survival time origin) is the
  encounter carrying each patient's **first qualifying fill**, so time zero never
  predates the exposure that defines the arm (immortal-time-safe).
- **`exposure_overlap_rule`:**
  - `"first_exposure"` (default): a patient exposed to both classes is assigned
    to whichever qualifying drug they filled first; patients exposed to neither
    are excluded. Keeps the most patients (standard pharmacoepi).
  - `"exclude"`: patients exposed to both qualifying classes are dropped
    (cleaner naive arms, fewer patients).
- **Cohort-flow funnel** becomes: Screened → Disease-eligible (codes) →
  cohort arm (exposure) → comparator arm (exposure).

## 6. Treatment classifier (`CFG$treatment_map` + filters)

The swappable core. `treatment_map` maps **display class = substrings**:

```r
treatment_map = list(
  Norepinephrine = "norepinephrine",   # list FIRST — before Epinephrine
  Epinephrine    = "epinephrine",
  Vasopressin    = "vasopressin"
)
```

Matching: drug name lower-cased, tested against each class's substrings **in
list order, first hit wins**. Order matters for substring collisions
(norepinephrine contains "epinephrine"); put the specific/priority class first.

- `systemic_routes`: keep only these routes (exact match). `NULL` = all routes.
  Filtering out topical/irrigation routes prevents contaminant drugs from
  inflating class counts (the neomycin-polymyxin-bacitracin lesson).
- `drug_exclude`: regex on lower-cased drug name to drop specific agents.
- `ttf_window_start`: hours-from-admit floor for time-to-first-exposure
  (negatives clamped to 0).
- `combo_window_start` / `combo_window_end`: window (hours from admit) for
  counting distinct classes toward combination therapy.
- `combo_min_classes`: distinct classes ≥ this ⇒ "combination therapy".

## 7. Endpoint & survival

- `primary_endpoint`: label for the report.
- Death observed if `!is.na(dod) | !is.na(deathtime) | expire_flag == 1`.
- Death datetime prefers `deathtime`, else `dod` at noon.
- Censoring at the last observed discharge per patient (stated in report).
- `landmark_times`: days at which KM survival + CI + n-at-risk are tabulated.

**Report landmark rates, not the median,** when the upper CI of the median is
undefined (small cohort). See `survival-guardrails.md`.

## 8. Statistical guardrails

- `epv_min` (default 10): events-per-variable floor. Cox / multivariable models
  run **only if** `n_events / length(cox_covariates) >= epv_min`; otherwise the
  pipeline emits descriptive + KM + landmark + univariable log-rank only, with
  an explicit suppression note in the report.
- `cox_covariates`: candidate covariates used only when EPV permits.
- `alpha`: nominal level; all p-values are reported as **exploratory**.
- `multiple_testing`: default `"none"` — no correction, stated in Methods.

## 9. Literature grounding (`CFG$literature_queries`)

Vector of queries handed to Biomni `LiteratureSearch` to ground the intro /
discussion and populate References. Keep specific: disease + treatment +
outcome. Treat one-line hits as discovery; verify details before quoting; never
fabricate citations.

## 10. Infographic mode (`CFG$infographic_mode`)

- `"composed_panel"` (**default, data-faithful**): the infographic is assembled
  from real computed numbers with ggplot2 — reproducible by construction.
- `"generated_shell"`: `GenerateImage` draws ONLY an empty design shell (layout,
  boxes, arrows, section framing) with **no numbers**; every value is overlaid
  programmatically from the computed outputs.

**Hard rule:** an image-generation model must never render actual numbers or
proportional bar heights. Doing so injects unverifiable, non-reproducible values
into a scientific deliverable. When in doubt, use `composed_panel`.

## 11. Branding (`CFG$palette`)

Phylo chart palette; the PDF builder additionally uses the full brand system
from the `pdf-report-generation` skill (gold `#D4A04A` accents, warm-gray grid,
Helvetica).

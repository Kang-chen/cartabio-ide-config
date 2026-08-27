# =============================================================================
# 00_config_template.R  --  SINGLE SOURCE OF TRUTH for an RWE cohort study
# -----------------------------------------------------------------------------
# Copy this file, rename it (e.g. my_study_config.R), and edit ONLY this file
# to adapt the pipeline to a new disease, treatment class, endpoint, or dataset.
# The analysis scripts (01-05), the infographic, and the PDF builder all read
# their parameters from the `CFG` list defined here. NOTHING disease-specific
# should ever be hardcoded in the analysis scripts.
#
# A filled example that reproduces the sepsis / MIMIC-IV worked example lives in
# examples/mimic_sepsis_config.R.
# =============================================================================

CFG <- list(

  # ---- 0. Study identity (used in report title / filenames) -----------------
  study_title   = "Real-World Cohort Study",   # human-readable report title
  cohort_label  = "Cohort",                    # short name for the disease cohort (e.g. "Sepsis")
  comparator_label = "Comparator",             # short name for the reference group (e.g. "Non-sepsis ICU")
  slug          = "rwe_study",                 # used to name output files

  # ---- 1. Input tables ------------------------------------------------------
  # Paths to patient-level tables (CSV or parquet). Set any optional table to
  # NA if unavailable. Column names are remapped in section 2.
  paths = list(
    patients    = NA_character_,   # one row per patient  (subject_id, sex, dob/age, dod)
    encounters  = NA_character_,   # one row per admission (subject_id, hadm_id, admittime, dischtime, ...)
    diagnoses   = NA_character_,   # long: subject_id, hadm_id, icd_code, icd_version
    drugs       = NA_character_,   # long: subject_id, hadm_id, drug, route, starttime
    icu_stays   = NA_character_,   # optional: subject_id, hadm_id, stay_id, intime, outtime, los
    out_dir     = "/mnt/results"   # where tables/, figures/, and the PDF are written
  ),

  # ---- 2. Column mapping ----------------------------------------------------
  # Map YOUR column names -> the canonical names the pipeline uses. Edit the
  # right-hand side to match your data. If your table already uses the canonical
  # name, leave it unchanged.
  cols = list(
    patients   = c(subject_id = "subject_id", sex = "gender", dob = "anchor_age",
                   dod = "dod"),
    encounters = c(subject_id = "subject_id", hadm_id = "hadm_id",
                   admittime = "admittime", dischtime = "dischtime",
                   deathtime = "deathtime", expire_flag = "hospital_expire_flag"),
    diagnoses  = c(subject_id = "subject_id", hadm_id = "hadm_id",
                   icd_code = "icd_code", icd_version = "icd_version"),
    drugs      = c(subject_id = "subject_id", hadm_id = "hadm_id",
                   drug = "drug", route = "route", starttime = "starttime"),
    icu_stays  = c(subject_id = "subject_id", hadm_id = "hadm_id",
                   stay_id = "stay_id", intime = "intime", outtime = "outtime",
                   los = "los")
  ),

  # `age_is_precomputed = TRUE` means the `dob` column already holds age in years
  # (as in MIMIC-IV `anchor_age`). FALSE means it holds a date of birth to be
  # differenced against the index date.
  age_is_precomputed = TRUE,

  # ---- 3. Cohort / eligibility definition (code-based) ----------------------
  # Diagnosis codes that qualify a patient. Provide, per ICD version, a vector
  # of code PREFIXES (startsWith) and/or EXACT codes. Codes are upper-cased and
  # trimmed before matching. Extend to as many code systems as needed; the
  # `icd_version` values in your data must match the names.
  #
  # ROLE depends on CFG$comparator (section 5):
  #   * "rest_of_population" / "rest_of_icu": these codes DEFINE THE COHORT and
  #     the comparator is everyone else (or the rest of the ICU).
  #   * "active_comparator": these codes define ELIGIBILITY (the disease pool,
  #     e.g. ALL type-2 diabetes = E11 + 250 together); the two treatment ARMS
  #     are then defined by DRUG EXPOSURE (section 6b), NOT by these codes.
  #
  # !! For a comparative DRUG study, define the two arms by EXPOSURE (section 6b
  # + comparator="active_comparator"). NEVER split arms by ICD-code vintage
  # (e.g. ICD-10 E11 vs ICD-9 250 for the same disease): coding version is an
  # era/site artifact, not a treatment distinction, and produces incoherent arms.
  cohort_codes = list(
    "9"  = list(prefix = character(0), exact = character(0)),
    "10" = list(prefix = character(0), exact = character(0))
  ),

  # Optional severity / subtype classification, most-severe-wins. Provide an
  # ordered list from MOST to LEAST severe; the first matching tier is assigned.
  # Set to NULL to skip. Each tier: name + per-version prefix/exact rules.
  severity_tiers = NULL,
  # Example shape (see examples/mimic_sepsis_config.R):
  # list(
  #   list(name = "Septic shock",  codes = list("9" = list(exact="78552"), "10" = list(exact="R6521"))),
  #   list(name = "Severe sepsis", codes = list("9" = list(exact="99592"), "10" = list(exact="R6520"))),
  #   list(name = "Sepsis",        codes = list("9" = list(prefix="038"),  "10" = list(prefix=c("A40","A41"))))
  # )

  # ---- 4. Index date & time origin ------------------------------------------
  # index_rule: "first_qualifying_encounter" (default) picks each patient's
  #   earliest admission that carries a qualifying code.
  # time_origin: where survival time zero starts.
  #   "index_admit"  -> admittime of the index encounter
  #   "index_icu_in" -> intime of the first ICU stay within the index encounter
  #                     (requires icu_stays); falls back to admit if missing.
  index_rule   = "first_qualifying_encounter",
  time_origin  = "index_admit",

  # require_icu = TRUE restricts the cohort to patients whose index encounter
  # has an ICU stay (set TRUE only if that is scientifically intended).
  require_icu  = FALSE,

  # ---- 5. Comparator / reference group --------------------------------------
  # "rest_of_population": everyone NOT meeting the cohort (code) definition.
  # "rest_of_icu": everyone with an ICU stay but NOT in the cohort (needs icu_stays).
  #     For each comparator patient the index encounter is their first (ICU) admission.
  # "active_comparator": EXPOSURE-BASED arms for comparative drug studies.
  #     cohort_codes (section 3) define ELIGIBILITY (the disease pool). The two
  #     arms are defined by drug exposure via section 6b:
  #        cohort arm     = patients whose exposure matches exposure_cohort_map
  #        comparator arm = patients whose exposure matches exposure_comparator_map
  #     Time zero = each patient's FIRST QUALIFYING FILL (new-user, immortal-time
  #     safe). Requires a drugs table + both exposure maps. This is the correct
  #     design when the contrast is drug A vs drug B (e.g. GLP-1 RA vs DPP-4i).
  comparator   = "rest_of_population",

  # ---- 6. Treatment classifier (SWAPPABLE) ----------------------------------
  # This is what makes the skill drug-class-agnostic. `treatment_map` maps a
  # display class name -> vector of lowercase substrings matched against the
  # drug name. Matching is done in list order, first hit wins, so put more
  # specific / higher-priority classes first (e.g. Norepinephrine before
  # Epinephrine to avoid the "epinephrine" substring collision).
  treatment_label = "Treatment",   # display name for this exposure class (e.g. "Systemic antibiotic", "Vasopressor")
  treatment_map = list(),   # e.g. list(Glycopeptide = "vancomycin", Carbapenem = c("meropenem","imipenem"))

  # Restrict to systemic administration routes (exact match, upper/mixed case as
  # in your data). Set to NULL to keep all routes. Exclude specific drugs (e.g.
  # topical contaminants) with `drug_exclude` (regex, matched on lowercase name).
  systemic_routes = c("IV","PO/NG","PO","PR","IV DRIP","IVPCA","NG","IM"),
  drug_exclude    = NULL,   # e.g. "bacitracin"

  # Exposure scope for the patient-level "on treatment" flag (Table 1 / comparison):
  #   "index_encounter" (default, more rigorous) — exposure counted only during
  #      the patient's index encounter (the episode under study).
  #   "any_encounter" — patient counted if ever exposed in any admission.
  # Detailed class/agent/timing summaries are always index-encounter scoped.
  treatment_exposure_scope = "index_encounter",

  # Time windows (hours from index admit) for treatment metrics:
  ttf_window_start   = -6,    # time-to-first-exposure: ignore doses earlier than this
  combo_window_start = -12,   # combination therapy: earliest dose counted (ED pre-window)
  combo_window_end   = 48,    # combination therapy: latest dose counted
  combo_min_classes  = 2,     # >= this many distinct classes = combination therapy

  # ---- 6b. Exposure-based arms (ONLY when comparator = "active_comparator") --
  # Define the two treatment arms by DRUG EXPOSURE (same shape as treatment_map:
  # display name -> lowercase substrings, first hit wins). The cohort_codes
  # (section 3) act as ELIGIBILITY; these maps split the eligible pool into arms.
  # Keep the two maps mutually exclusive at the drug level (different drug
  # classes). Leave both empty for the code-based comparators.
  #   e.g. exposure_cohort_map     = list(`GLP-1 RA` = c("semaglutide","dulaglutide","liraglutide")),
  #        exposure_comparator_map = list(`DPP-4i`   = c("sitagliptin","linagliptin","saxagliptin"))
  exposure_cohort_map     = list(),
  exposure_comparator_map = list(),
  # How to handle a patient exposed to BOTH arms' drug classes (new-user design):
  #   "first_exposure" (DEFAULT): assign to whichever qualifying drug was filled
  #        FIRST; time zero = that first fill. Patients exposed to neither are
  #        excluded. Keeps the most patients; standard pharmacoepi practice.
  #   "exclude": drop patients exposed to both qualifying classes (cleaner arms,
  #        fewer patients; use when switching is common and you want naive arms).
  exposure_overlap_rule   = "first_exposure",

  # ---- 7. Endpoint & survival -----------------------------------------------
  # primary_endpoint: label only, used in the report.
  primary_endpoint = "In-hospital mortality",

  # Death observation is derived as: (!is.na(dod)) | (!is.na(deathtime)) | expire_flag==1
  # Censoring: at the last observed discharge per patient (documented in report).
  landmark_times = c(7, 14, 30, 60, 90),   # days for KM landmark survival table

  # ---- 8. Statistical guardrails --------------------------------------------
  # EPV = events per variable. Cox / multivariable models are SUPPRESSED unless
  # (n_events / n_candidate_covariates) >= epv_min. Below this, only descriptive
  # + Kaplan-Meier + landmark rates + univariable log-rank are produced.
  epv_min          = 10,
  cox_covariates   = character(0),  # candidate covariates IF EPV allows (else ignored)
  alpha            = 0.05,          # nominal; p-values reported as EXPLORATORY
  multiple_testing = "none",        # documented: no correction by default

  # ---- 9. Literature grounding ----------------------------------------------
  # Terms passed to Biomni LiteratureSearch to ground intro/discussion and build
  # the References section. Keep specific: disease + treatment + outcome.
  literature_queries = c(),   # e.g. c("sepsis antibiotic timing mortality", "sepsis vasopressor outcomes")

  # ---- 10. Infographic mode -------------------------------------------------
  # "composed_panel" (DEFAULT, data-faithful): infographic is assembled from the
  #   real computed numbers with ggplot2. Reproducible by construction.
  # "generated_shell": GenerateImage draws ONLY an empty design shell (layout,
  #   boxes, arrows) with NO numbers; every value is overlaid programmatically.
  #   NEVER let an image model render actual numbers or proportional bars.
  infographic_mode = "composed_panel",

  # ---- 11. Branding (from pdf-report-generation skill) -----------------------
  palette = c("#0279EE","#FF9400","#75A025","#D4A04A","#FD9BED","#000000","#8A8378")
)

# Convenience: allow scripts to `source()` this file and get CFG in the env.
if (!interactive()) invisible(CFG)

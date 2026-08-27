# RWE Cohort Study Methodology

This note explains the study-design reasoning behind the pipeline. It is meant to
be read alongside `config-reference.md` (what each knob does) and
`survival-guardrails.md` (how the survival/regression guardrails work). Citations
point to the methodological literature; when you run the skill, refresh them with
Biomni `LiteratureSearch` on your own disease/treatment terms.

---

## 1. What this pipeline is (and is not)

It is a **retrospective, observational cohort analysis** on structured EHR /
claims-style data: define a cohort by diagnosis codes, characterize it at
baseline, describe treatment patterns, and follow patients for a time-to-event
endpoint, comparing against a contemporaneous comparator group.

It is **not** a causal-inference engine. Observational real-world data are prone
to **confounding by indication** — sicker patients receive different treatment,
so treated-vs-untreated (or cohort-vs-comparator) differences conflate the effect
of the exposure with the reason it was given [2, 6]. Everything the pipeline
produces is therefore framed as **descriptive and hypothesis-generating**. If a
causal contrast is the goal, the appropriate next step is a **target-trial
emulation** with explicit eligibility, treatment strategies, and a defined time
zero, plus confounding adjustment (propensity or regression) [2, 5].

## 2. Cohort definition (code-based phenotyping)

The cohort is defined by ICD (or other) codes via `CFG$cohort_codes`
(per-version prefix + exact lists). Code-based phenotypes are fast and
transparent but **imperfect**: administrative codes can over- or under-capture a
condition, and coding practices vary by site and over time [1, 6]. Practical
guardrails baked into the design:

- **One index encounter per patient.** The unit of follow-up is the patient's
  first qualifying encounter (`index_rule = "first_qualifying_encounter"`), not
  every admission. This avoids counting the same patient repeatedly and makes the
  denominator unambiguous.
- **Severity/subtype tiers are optional and most-severe-wins** so a patient with
  multiple codes is classified once, at their highest tier.
- **Validate the phenotype.** The single highest-value robustness step for a
  real study is to check the code definition against chart review or a published,
  validated phenotype, and to run a sensitivity analysis with an alternative code
  set [1, 7]. This is why "validate the cohort definition" is the first
  recommended next step in every generated report.

## 3. Comparator selection

`CFG$comparator` chooses the reference group: the rest of the population, or (for
ICU-focused studies) the rest of the ICU population. The comparator's index
encounter is selected on the **same rule** as the cohort (first, or first-ICU,
admission), so the two groups are aligned on time zero. Misaligned time zero
(immortal-time bias) is one of the most common and damaging errors in RWE — a
patient cannot be "in" a group before the event that defines their entry [5, 6].
When the study is ICU-anchored (`time_origin = "index_icu_in"` or
`comparator = "rest_of_icu"`), the pipeline selects each comparator patient's
**first admission that actually has an ICU stay**, so the comparator is a true
ICU population rather than a general-ward one.

### 3a. Comparative drug studies: the active-comparator, new-user design

When the question is *drug A vs drug B* (e.g. GLP-1 RA vs DPP-4i), the arms must
be defined by **what patients received**, not by their diagnosis code. Two rules
follow directly:

- **Arms = exposure, eligibility = disease codes.** Set
  `comparator = "active_comparator"`; put **all** the disease codes in
  `cohort_codes` (eligibility) and define the two arms with
  `exposure_cohort_map` / `exposure_comparator_map`. Splitting arms by ICD-code
  *vintage* (ICD-10 `E11` vs ICD-9 `250` for the same disease) is a category
  error: coding version reflects the encounter's calendar era and site, not the
  treatment, and yields arms that differ by artifact rather than by drug.
- **Active comparator, not "untreated".** Comparing initiators of drug A to
  initiators of an active drug B (both indicated for the same condition) reduces
  confounding by indication relative to a treated-vs-untreated contrast, because
  both groups have crossed the same treatment-decision threshold [2, 5, 6]. The
  comparator here is therefore a *specific drug class*, and `comparator` must
  match the arms actually analyzed (do not narrate an active comparator while
  configuring "rest of population").
- **New-user, immortal-time-safe time zero.** Each patient's time origin is their
  **first qualifying fill** of the arm-defining drug, so follow-up starts at
  initiation and no one is "in" an arm before the exposure that defines it [5, 6].
- **Overlap handling** (`exposure_overlap_rule`): patients exposed to both
  classes are assigned by first exposure (default) or excluded — state which.

This remains a **descriptive** contrast: even a clean active-comparator design
does not by itself remove residual confounding; a causal estimate still needs
pre-specified adjustment (propensity/regression) or target-trial emulation.

## 4. Treatment/exposure classification

Exposure is derived from medication **orders**, classified into user-defined
classes (`CFG$treatment_map`, first-hit-wins). Two design choices matter:

- **Systemic routes only** (`CFG$systemic_routes`) — topical/irrigation orders
  are excluded so "on treatment" means a systemic therapeutic course, not a skin
  prep. `CFG$drug_exclude` removes named contaminants.
- **Exposure scope** (`CFG$treatment_exposure_scope`): `index_encounter` (default,
  more rigorous — exposure counted only during the episode under study) vs
  `any_encounter` (ever-exposed). These answer *different* questions; pick
  deliberately and report which you used.

Orders are a proxy for administration; a dispensed/administered signal (e.g. MAR
data) is stronger when available. This limitation is stated in every report.

## 5. Baseline table and exploratory comparisons

Table 1 is auto-typed: continuous variables as **median [IQR]** with a Wilcoxon
rank-sum test, binary variables as **n (%)** with **Fisher's exact test**. These
tests are **exploratory**: they flag differences worth thinking about, they are
not confirmatory hypothesis tests. No multiple-testing correction is applied by
default (`CFG$multiple_testing = "none"`), and the report says so [3, 6]. With
many comparisons, treat small p-values with appropriate skepticism.

## 6. Reporting standards

Observational EHR studies should be reported against a structured checklist.
**RECORD** (an extension of STROBE for routinely-collected health data) is the
relevant standard, and endorsement of it remains inadequate in practice — which
is precisely why reporting discipline matters [1]. The generated PDF is
structured to surface the elements reviewers look for: data source and cohort
definition, index/time-zero rule, comparator, exposure definition, endpoint,
statistical approach, and explicit limitations. Pre-registration of observational
protocols is increasingly encouraged to reduce selective reporting [4].

## 7. The honest-defaults philosophy

The skill deliberately defaults to the **most defensible** analysis rather than
the most impressive one:

- Descriptive + Kaplan-Meier + landmark rates + univariable log-rank always run.
- Multivariable Cox runs **only** when the events-per-variable threshold is met
  (see `survival-guardrails.md`); otherwise it is suppressed with a printed note.
- The infographic is assembled from the **real computed numbers** (never
  rendered by an image model), so it cannot drift from the analysis.

These defaults make it hard to accidentally over-claim from a small
observational cohort.

---

### References

1. Zhao R, Zhang W, Zhang Z, et al. Evaluation of reporting quality of cohort
   studies using real-world data based on RECORD: a systematic review. *BMC Med
   Res Methodol.* 2023. doi:10.1186/s12874-023-01960-2
2. Chen H-Y, Chang R, Yong S-B. The essential role of emulated clinical trial
   designs in TriNetX studies: avoiding confounding by indication. *Int J Rheum
   Dis.* 2025. doi:10.1111/1756-185x.70450
3. Zoccali C, Tripepi G. Real-world evidence and observational studies:
   methodological challenges in clinical research. *Eur J Clin Invest.* 2025.
   doi:10.1111/eci.70153
4. Berlin JA, Fihn SD. Encouraging the registration of observational studies.
   *JAMA Netw Open.* 2025. doi:10.1001/jamanetworkopen.2025.24181
5. Powell M, Koenecke A, Byrd JB, et al. Ten rules for conducting retrospective
   pharmacoepidemiological analyses. *Front Pharmacol.* 2021.
   doi:10.3389/fphar.2021.700776
6. Stürmer T, Wang T, Golightly YM, et al. Methodological considerations when
   analysing and interpreting real-world data. *Rheumatology (Oxford).* 2020.
   doi:10.1093/rheumatology/kez320
7. Weaver J, Voss EA, Cafri G, et al. The necessity of validity diagnostics when
   drawing causal inferences from observational data. *BMC Med Res Methodol.*
   2024. doi:10.1186/s12874-024-02428-7

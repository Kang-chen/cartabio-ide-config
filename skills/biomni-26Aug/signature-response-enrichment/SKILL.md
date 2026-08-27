---
id: "skill_b65a07539d1b4f5e8955b98c7aaca5fe"
name: "signature-response-enrichment"
description: "Use to test whether patients who fail a treatment retain higher activity of a specified gene signature. Runs GSVA change-from-baseline across on-treatment time points, compares responders with non-responders at endpoint, and checks directional replication across independent bulk-transcriptomic cohorts."
category: "transcriptomics"
visibility: "public"
starting-prompt: "Test whether residual activity of my gene signature on treatment marks patients who fail the drug, and check whether the pattern reproduces across independent cohorts."
---

signature-response-enrichment

What this skill does

Answers one recurring question: does residual activity of a gene signature, on treatment, mark patients who fail a drug? It generalizes a psoriasis/TYK2/adalimumab replication into a drug-, disease-, and signature-agnostic workflow.

The logic: score each sample for the signature(s) with GSVA, track change from baseline over on-treatment timepoints, and test whether non-responders (NR) retain higher signature activity than responders (R) at the treatment endpoint — then ask whether that direction reproduces across independently discovered cohorts.

Inputs (user contract)

Required:


drug — e.g. adalimumab. Drives discovery queries and cohort/arm filtering.
gene_signatures — one or more named gene sets. Accept: inline symbol lists, a GMT file, or MSigDB set IDs. Ambiguous tokens must be resolved and documented (worked example: IFNA -> IFNA1+IFNA2, IL18R -> IL18R1).
disease — e.g. psoriasis. Scopes discovery, tissue, and severity-metric expectations.


Optional (documented defaults):


response_threshold = 0.75 (75% improvement from baseline)
response_threshold_sensitivity = 0.90 — a stricter threshold re-run reported alongside the primary (set to none to skip). Run and report this by default.
response_metric = auto-detect the cohort's continuous severity field (PASI, SLEDAI, DAS28, tumor burden, ...); fall back to a categorical responder/non-responder label
tissue = target tissue (e.g. lesional skin)
context_panel = MSigDB Hallmark (50 sets) + keyword-filtered Reactome immune sets
focused_panel = a curated, disease/pathway-relevant subset of the signatures + context sets (worked example: 2 TYK2 signatures + ~15 immune modules). Reported per-module in the results, not just used for interpretation. Defaults to the signatures plus the most disease-relevant context sets when not supplied.
pharmacodynamic_cohorts = all — analyze every qualifying paired-pre/post or drug-arm comparator cohort as a pharmacodynamic control, not just one. (first to keep the legacy single-cohort behavior.)
pd_comparator_timepoint = ask — for a pharmacodynamic comparator arm with more than one on-treatment timepoint, which timepoint to use for the pre/post contrast (see Stage 5.6). ask = prompt the user (recommending the latest timepoint with adequate n); latest_adequate_n = auto-pick the latest timepoint meeting the n floor (non-interactive fallback); match_paired_window = align to the paired PD cohort's sampling window for cross-cohort comparability; or an explicit timepoint label.
min_set_size = 3, fdr_alpha = 0.05
fisher_shared_parent = ask — how to headline the cross-cohort Fisher combination when two or more input cohorts share a parent study (see Stage 5.2). ask = pause and ask the user; independent = independence-aware headline (rigorous default if non-interactive); all = combine all splits as the headline (pseudo-replicated — discouraged). Regardless of choice, both the independent-only and all-splits values are always reported.
primary_cohort / validation_cohort overrides


Scope & assumptions


Bulk transcriptomics (RNA-seq or microarray) with longitudinal baseline + on-treatment sampling. Single-cell / spatial are out of scope for v1.
Discovery uses public repositories (GEO via NCBI eutils + ArrayExpress/BioStudies) through their standard APIs; the agent curates the candidate list (this is not fully autonomous dataset selection).
Helper scripts under scripts/ are starting-point implementations you adapt to each dataset's real (often messy) metadata. This file (SKILL.md) is the authoritative procedure.
Never fabricate data or citations. Every external fact (PMID/DOI/accession) must be confirmed against the primary source before it enters a report.


Validation status (honest disclosure)

The helper scripts have been syntax/parse-checked (R parse(), Python --help) and the statistical parameters were verified against the worked-example run, but the packaged scripts have not been executed end-to-end on real data as an integrated pipeline — the worked example was produced by the original bespoke analysis, then generalized into these helpers. Treat the first run on any new dataset as a shakedown: expect to adapt each script to that cohort's real (messy) metadata, and confirm at runtime that the Bioconductor stack resolves (GSVA 1.50.5, limma, GSEABase, edgeR) and that each stage's outputs are sane before trusting the report. The Stage 7 verify-before-trust gate is what catches integration problems — do not skip it.

Environment note (read before running)

GSVA on Bioconductor >= 3.19 pulls SpatialExperiment -> magick, which needs the libmagick system library that is frequently unavailable. Pin GSVA 1.50.5 (Bioconductor 3.18) — it keeps the classic gsva() / gsvaParam() API used here and has no SpatialExperiment dependency. scripts/run_gsva.R documents the source install.

Version enforcement (do not skip). The GSVA version materially changes absolute scores and therefore borderline p-values (in the worked example, the pivotal cross-platform cohort's one-sided p moved from ~0.04 under 1.50.5 to ~0.095 under 2.0.0 — enough to flip a headline across 0.05). Stage 0 must hard-fail if the resolved version is not 1.50.5; never silently proceed on a fallback version. If 1.50.5 cannot be installed, stop and surface the problem rather than running on another version.

Compute sizing: discovery + per-cohort GSVA / limma / CAMERA on bulk expression is light (minutes, < 8 GB RAM) and runs on the default worker-0. Escalate with ManageMachine only when many or very large RNA-seq count matrices must be processed at once.

Procedure (8 stages)

Track progress with TodoWrite. Run the verify-before-trust gate (Stage 7) before declaring done.

Stage 0 — Inputs & environment


Parse drug, disease, gene_signatures (+ optional params). Echo them back.
Confirm/install GSVA 1.50.5 (see scripts/run_gsva.R). Assert the version: fail loudly and stop if packageVersion("GSVA") != "1.50.5" — do not proceed on any other version (see Environment note). Confirm limma, GSEABase, edgeR.
Create the output tree under /mnt/results/<run>/: data/, figures/, tables/, and the report path.


Stage 1 — Automated discovery


Run scripts/discover_datasets.py --drug <drug> --disease <disease>. It queries:

NCBI GEO via eutils esearch/esummary on the gds database, and
ArrayExpress / BioStudies (REST) — do not skip this; pivotal cohorts (e.g. PSORT E-MTAB-14509) are not in GEO.



Cast a wide net. Capture per candidate: accession, platform, organism, treatment(s), tissue/design, timepoints, whether a per-patient severity metric is present, PMID. Also retain right-disease / right-tissue but wrong-drug cohorts (other biologics for the same disease) — they belong in the audit trail as documented exclusions, not silent drops.
Write the discovery catalog CSV (data/<disease>_<drug>_catalog.csv) — all candidates, before filtering.


Stage 2 — Agent curation

Apply inclusion rules to each candidate:


(a) the drug is present as an arm,
(b) longitudinal baseline + on-treatment sampling in the target tissue,
(c) a recoverable per-patient response (numeric severity preferred; categorical R/NR acceptable).


Mark each Included/Excluded with a one-line reason, and assign a role: primary, validation (e.g. an internal discovery/refinement split, or a cross-platform cohort), or pharmacodynamic (paired pre/post OR a drug-arm comparator, used to show the signature is drug-responsive even without response labels).


List near-miss cohorts explicitly. Right-disease/right-tissue but wrong-drug series must appear in the catalog with an explicit "Excluded — not <drug> (<actual drug>)" reason, so Table 1 documents the full landscape screened, not only the winners.
Include all qualifying pharmacodynamic cohorts when pharmacodynamic_cohorts = all (the default): every paired pre/post cohort and every same-drug comparator arm with on-treatment sampling gets the pharmacodynamic role and is carried into Stage 5.6 — do not defer them to "next steps." A cohort marked pharmacodynamic in the catalog must actually be analyzed, or its row must say Excluded, never a contradictory "Included but unused."


Write decisions + reasons back into the catalog CSV. This catalog becomes Table 1 of the report.

Stage 3 — Response definition

For each included, response-labeled cohort run scripts/build_response_table.py:


pct_improve = (baseline - endpoint) / baseline on the severity metric;
responder if pct_improve >= response_threshold - 1e-9 (the -1e-9 avoids floating-point boundary flips — this materially changed one boundary patient in the worked example);
if only categorical labels exist, use them and flag the cohort as label-based.


Repeat the split at response_threshold_sensitivity (default 0.90) if set, saving it as a parallel labeling for the Stage 5 sensitivity pass. Save per-patient response tables (both thresholds) to data/ (part of the rerunnable bundle). Report the R/NR split per cohort per threshold.

Stage 4 — GSVA scoring

Run scripts/run_gsva.R per cohort:


Alias-map each signature to the cohort's feature space; report per-cohort coverage (e.g. 21/21, 19/20); drop any set with < min_set_size mapped genes and flag it.
Score all samples with GSVA:
gsvaParam(..., kcdf="Gaussian", minSize=3, maxSize=500)
on log-normalized / log-CPM expression. Fractional tximport counts use the log-CPM + Gaussian route (not a Poisson kernel).
Add the context_panel sets (Hallmark + Reactome immune) scored the same way, and tag the focused_panel subset for per-module reporting.
Save GSVA score matrices to data/ (bundle).


Stage 5 — Statistics (full template; extras auto-skip)

Run scripts/delta_gsva_stats.R, then scripts/camera_endpoint.R and scripts/gene_de.R:


Primary — change-from-baseline ΔGSVA. Two steps: (i) form each patient's paired difference dGSVA(t) = GSVA(t) - GSVA(baseline) (within-patient change), then (ii) at the endpoint compare the NR patients' ΔGSVA values against the R patients' ΔGSVA values with a two-sample Wilcoxon rank-sum test (wilcox.test(nr, r)), plus a t-test alongside. Direction = NR - R (positive => higher in non-responders). This is an unpaired between-group test (NR and R are different, usually unequal-size groups — e.g. 9 NR vs 16 R); the only "paired" element is the per-patient baseline subtraction in step (i). Do not call it a "paired Wilcoxon" and do not pass paired=TRUE here — label figures and tables "Wilcoxon rank-sum (unpaired, NR vs R)". (A genuinely paired Wilcoxon is used only in the separate pharmacodynamic module, step 6, where the same patients are measured pre and post.) Report the primary signatures and every focused_panel module, one row per set × cohort.
Cross-cohort concordance — Fisher's method on one-sided p-values testing the NR > R hypothesis:


 X <- -2 * sum(log(pvals)); p <- pchisq(X, df = 2 * length(pvals), lower.tail = FALSE)

With a single cohort, report this as n/a (nothing to combine).

Independence caveat (must check and disclose). Fisher's method assumes the combined p-values come from independent cohorts. Sub-cohorts that are splits of one parent dataset (e.g. a discovery/refinement split of the same consortium study, as with PSORT E-MTAB-14509) are not independent — combining them inflates the apparent evidence (pseudo-replication). scripts/delta_gsva_stats.R accepts a --cohort-parent map and flags shared-parent inputs in its concordance output.

Always compute and report both an independent-only value (one representative cohort per parent + all genuinely independent cohorts) and an all-splits value, side by side, with per-cohort effects and one-sided p-values so a reader can see whether the signal is broad or driven by one cohort.

Which one carries the headline is governed by fisher_shared_parent, and this decision is only live when shared-parent splits are actually detected:


ask (default) — when the run detects that two or more contributing cohorts share a parent study, pause and ask the user (via AskUserQuestion) which value should be the headline: the independence-aware value (rigorous; recommended) or the all-splits value (reproduces a larger apparent effect but is pseudo-replicated). Present the two numbers in the prompt so the choice is informed. If the run is non-interactive, fall back to independent.
independent — headline the independence-aware value; all-splits reported only as a sensitivity check.
all — headline the all-splits value. Permitted but discouraged; when chosen, the report must state prominently that the headline is pseudo-replicated and show the independence-aware value beside it.


If no inputs share a parent, all contributing cohorts are independent and the distinction is moot (report the single combined value). Never let a borderline Fisher p (just under 0.05) carry the headline on its own — pair it with the ΔGSVA direction and the CAMERA/DE evidence, and state the caveat, regardless of the setting chosen.


CAMERA — competitive gene-set test at the endpoint, accounting for inter-gene correlation (limma::camera). Auto-skip a cohort that lacks a usable design; note that the absolute endpoint contrast is partly confounded by residual disease activity (state this in the report).
Per-gene DE — limma-voom for RNA-seq (voom(dge) -> lmFit(E, model.matrix(~grp)) -> eBayes) / limma for microarray. Auto-skip if unsupported.
Multiplicity — dual FDR. Report Benjamini-Hochberg FDR two ways, in separate columns, throughout: (a) a focused-family FDR across the focused_panel sets × timepoints, and (b) a global-family FDR across the full scored set list × timepoints. Report nominal (* p<0.05) and FDR (** FDR<0.05) separately — never conflate, and never collapse the two FDR families into one number.
Pharmacodynamic module — for each cohort assigned the pharmacodynamic role (all of them when pharmacodynamic_cohorts = all): show the signature moves with treatment as drug-responsiveness evidence. For a paired pre/post cohort (same patients before and on treatment, e.g. GSE74697) a genuinely paired test is appropriate: wilcox.test(pre, post, paired = TRUE) on the matched pairs. For a drug-arm comparator with longitudinal on-treatment sampling (e.g. a VOYAGE-1-style ADA arm), test the on-treatment timepoint vs baseline (paired where patients are matched, else report means +/- SEM descriptively).
Timepoint selection for a multi-timepoint comparator arm (pd_comparator_timepoint). When such an arm has more than one on-treatment timepoint, the choice materially changes the result — early timepoints can show incomplete suppression and understate drug-responsiveness, so the timepoint must not be picked silently. Behavior:

ask (default) — pause and ask the user (via AskUserQuestion) which on-treatment timepoint to use, presenting the available timepoints with their matched-pair n, and phrasing the recommendation as: "For a comparator arm with multiple on-treatment timepoints, use the latest timepoint with adequate n (deepest expected suppression); alternatively match the paired-cohort window for cross-cohort comparability."
latest_adequate_n — auto-pick the latest on-treatment timepoint whose matched-pair n still meets the n floor (guarding against dropout); the non-interactive fallback for ask.
match_paired_window — align to the paired PD cohort's sampling window (e.g. ~wk4) for comparability across PD cohorts.
an explicit timepoint label — use exactly that timepoint.


Regardless of the choice, always report the full on-treatment trajectory (means ± SEM) alongside the chosen contrast, so the timepoint decision is transparent and a reader can see whether the signal depends on it.
This is the only place paired = TRUE is correct; it is distinct from the unpaired NR-vs-R endpoint test in step 1.
Threshold sensitivity — repeat steps 1–2 (and, where cheap, 3) using the response_threshold_sensitivity labels (default 0.90). Report as an explicit sensitivity section: direction concordance, strongest single signals, and whether anything survives FDR. Flag that stricter thresholds shrink responder groups and widen intervals, so these are fragile.


Stage 6 — Figures & report

Figures via scripts/make_figures.R (each PNG and SVG, editable text):


NR-vs-R ΔGSVA heatmap — main body = change-from-baseline per timepoint × cohort across the focused panel (signatures + modules); optional right strip = absolute endpoint contrast, explicitly labeled as confounded.
Volcano(es) — per-gene DE (limma-voom).
Pharmacodynamic — pre/post (or arm-trajectory) signature change, one panel per pharmacodynamic cohort.
Trajectories — ΔGSVA over on-treatment timepoints, NR vs R, mean +/- SEM.


Report via scripts/build_report.py (ReportLab). Order:


Title
Table 1 = discovery catalog (all screened datasets + include/exclude reasons, including near-miss wrong-drug cohorts) — evidence base before claims
Summary + headline callout
Methods — datasets, signatures+coverage, GSVA settings (incl. version), and a "which test produced which result" mapping block
Results — analysed-cohort table, ΔGSVA endpoint (signatures + focused-panel modules, with dual-FDR columns), CAMERA, per-gene DE, pharmacodynamic (all cohorts), threshold-sensitivity section
Limitations, Next steps, References


Label every figure, table, and results subheading with the method that produced it (GSVA ΔGSVA / CAMERA / limma-voom / Fisher). Framing: present the ΔGSVA concordance and the FDR-adjusted results side by side; report both the independent-only and all-splits Fisher values and name which one is the headline (per fisher_shared_parent); state sensitivity to small samples and boundary patients honestly. Do not let the headline rest solely on a confounded absolute endpoint or a near-null effect — report both and caveat.

Stage 7 — Verify-before-trust (MANDATORY gate)

Run scripts/validate_report.py and the media-check loop:


Reconcile every headline number in the PDF against the source CSVs; fail loudly on any mismatch. Include the GSVA version and both Fisher values in this reconciliation.
Table-numbering integrity — tables contiguous, no duplicates; every in-text Table N resolves.
Media-check every figure page — render with pymupdf and run the visual QA check (the Read media-output-check). Regenerate on fail (glyph/layout/clipping) and re-check. Do not ship a figure that fails.
Confirm every external fact (PMID / DOI / accession) against the primary source or the run record. Never reconstruct a citation from memory.
Consistency check on test labeling: confirm the NR-vs-R test is described as unpaired Wilcoxon rank-sum everywhere (no stray "paired Wilcoxon" on the endpoint contrast), and that "paired" appears only in the pharmacodynamic module.
Glyph safelist (ReportLab/Helvetica):

SAFE: &mdash; &ndash; Δ(&#916;) α(&#945;) β(&#946;) γ(&#947;) κ(&#954;) •(&#8226;) &minus; ×(&#215;) ±(&#177;) ≈(&#8776;) ≥(&#8805;) ′(&#8242;) →(&rarr;) &lt; &gt; &amp; &lsquo; &rsquo;
UNSAFE — never use (render as .notdef black squares): &nbsp;, &#8209; (non-breaking hyphen). Use plain ASCII space / hyphen instead.





Deliverables (every run)


Media-checked PDF report (primary).
Standalone figures + tables — PNG + SVG figures and all result tables as CSV in figures/ and tables/.
Discovery-catalog CSV — the full screened-dataset catalog with include/exclude reasons (auditable on its own).
Rerunnable analysis bundle — saved GSVA score matrices, per-patient response tables (both thresholds), per-gene DE tables, dual-FDR stat outputs, and both Fisher values, so the analysis can be re-inspected/extended without recomputation.


Defaults reference

See references/METHODS.md for the verbatim parameter/formula reference and references/worked_example.md for the TYK2/adalimumab/psoriasis walkthrough and expected artifacts. Statistical defaults are all configurable via the optional inputs above.

Graceful degradation (documented behavior)


Signature doesn't map (coverage < min_set_size) -> drop the set, flag in coverage report and Methods.
No numeric severity metric -> use categorical R/NR labels, flag cohort as label-based.
Single cohort only -> run per-cohort tests; concordance (Fisher) reported n/a; fisher_shared_parent is moot.
No shared-parent splits -> all cohorts independent; report the single combined Fisher value; skip the ask prompt.
Non-interactive run with fisher_shared_parent = ask -> fall back to independent (rigorous) and note the fallback.
CAMERA / limma-voom unsupported for a cohort -> auto-skip with an explicit note; the ΔGSVA headline still stands.
No paired/comparator pharmacodynamic cohort -> omit the pharmacodynamic module (note its absence).
Comparator PD arm with a single on-treatment timepoint -> use it; pd_comparator_timepoint is moot (no prompt).
Non-interactive run with pd_comparator_timepoint = ask -> fall back to latest_adequate_n and note the fallback.
response_threshold_sensitivity = none -> skip the Stage 5.7 sensitivity pass (note its absence).

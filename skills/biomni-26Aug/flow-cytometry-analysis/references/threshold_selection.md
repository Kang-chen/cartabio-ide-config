# Data-driven threshold selection, review, and multivariate gating

This reference explains **how QC cutoffs are chosen** in `01_load_and_qc.R` via `gating_engine.R`:
data-driven (density-valley) 1D cutoffs, an honesty guard that refuses to invent a boundary,
control/FMO anchoring, 2D joint gates, and the editable-template two-pass review workflow. It
complements `qc_gating.md` (the *gate-gently* philosophy, which still governs everything here).

---

## Why not fixed percentiles?

Fixed-percentile cutoffs (e.g. "drop the top 5% on the viability channel") are convenient but
**biologically arbitrary**: the true positive/negative boundary depends on staining intensity,
compensation, instrument, and the actual fraction of dead cells — none of which is 5% by
assumption. A cutoff placed at the **density valley** (antimode) between the negative and positive
modes follows the data, which is exactly what a human does by eye on a histogram. This is the
canonical behavior of flowDensity's `deGate` (Malek et al. 2015) and reduces manual workload while
tracking expert gates (Mair et al. 2016); the same automated approach now scales to high-throughput clinical datasets (Lee et al. 2019). Method-comparison guidance (Liu et al. 2024) likewise
favors distribution-aware cutoffs over hardcoded quantiles.

The leverage is highest for the **live/dead** gate: an incorrectly placed viability cutoff
mis-classifies live vs dead and thereby **biases every downstream population and readout**
(Petrunkina & Harrison 2011). Getting this one boundary right — and *showing* it for review — is the
single most valuable thing this engine does.

---

## The 1D methods (`--gate-method`)

`estimate_threshold_1d()` computes a cutoff on the **transformed** channel (arcsinh for
fluorescence/mass, linear for scatter — so valleys are visible on the scale people actually gate on):

| method | how the cutoff is placed | when to use |
|---|---|---|
| `valley` | KDE antimode: lowest-density point between the two largest density peaks | default; clear bimodal separation |
| `gmm` | 2-component Gaussian mixture (mclust) antimode between component means | overlapping modes where a smooth mixture fits better than a raw KDE dip |
| `otsu` | Otsu between-class variance maximization (image-thresholding classic) | robust bimodal split when KDE is noisy |
| `percentile` | fixed quantile (the legacy fallback) | conservative default when no real valley exists |
| `control` | anchored to an unstained/FMO control (see below) | positive/negative calls where a control was acquired |
| `auto` | `valley` with the **honesty guard**, falling back to `percentile` | recommended default |

`direction` ∈ {`keep_below`, `keep_above`, `keep_between`}. Legacy-compatible fallback percentiles
are preserved: `keep_below` → 95th, `keep_above` → 2nd, so on already-clean data `auto` reproduces
the previous behavior.

---

## The honesty guard (do not invent a boundary)

A valley method will always return *some* minimum — even on a **unimodal** channel where there is no
real negative/positive split. Placing a cutoff there is worse than useless: it fabricates a
population boundary. Two guards prevent this:

1. **Unimodality test** — Hartigan's dip test (`diptest`; KDE peak-count fallback if unavailable).
   If the channel is unimodal at `--dip-alpha` (default 0.05), **no valley is claimed**.
2. **Valley-depth test** — the valley must be deep enough relative to its flanking peaks
   (normalized depth = `1 - valley_height / min(flanking_peak_heights)`) to clear
   `--valley-min-depth` (default 0.10).

When either guard fails, the engine **falls back to a conservative percentile** and sets a `status`
of `REVIEW_unimodal` or `REVIEW_shallow`. It never silently asserts a spurious cutoff. `<10` finite
events → `REVIEW_too_few`. This is the same "state it and flag it" discipline as the *honesty rule*
in `qc_gating.md`.

---

## Control / FMO anchoring (`--controls`)

When an unstained or FMO (fluorescence-minus-one) control is supplied, the positive/negative
boundary is anchored to a high percentile (default 0.99) of the control distribution rather than to
the sample itself — the reference-standard way to determine positivity from controls (Maecker & Trotter 2006). A
control-anchored cutoff **always takes precedence** over a data-driven one when a matching control
channel is provided (status `control`; `REVIEW_no_control` if the channel can't be matched).

`controls.csv` schema (see `assets/controls_template.csv`):

| column | meaning |
|---|---|
| `channel` | regex matched against FCS channel names (e.g. `Live_Dead|Viability|Zombie`) |
| `control_file` | path to the control `.fcs` |
| `control_type` | `unstained` or `fmo` |
| `percentile` | optional; percentile of the control used as the cutoff (default 0.99) |

---

## Multivariate / 2D joint gates (`--multivariate on`)

Single-channel gating misses structure that only appears jointly. Two gates default to 2D:

- **debris** — FSC-A × SSC-A, keep the main-population ellipse.
- **live/dead** — viability × scatter (flow) or Pt × DNA (CyTOF), emitted as a **diagnostic**
  (`apply=N` by default; enable per row in the template).

`estimate_gate_2d()` dispatches to flowClust model-based clustering when available (Lo et al. 2009),
falling back to a robust Mahalanobis ellipse from a **minimum-covariance-determinant** location/
scatter estimate (`MASS::cov.rob`, method `mcd`; robust model-based clustering, Lo et al. 2008). The
fallback is deliberately **permissive** (keep-on-failure) so a numerical hiccup never silently
deletes cells — consistent with the gate-gently rule.

---

## Review workflow: propose -> edit -> apply (`--gate-review`)

Every proposed cutoff is written to an **editable template** and each gate to a **diagnostic figure**
so a human can confirm or override before results are trusted — the template-driven, reproducible
philosophy of OpenCyto (Finak et al. 2014), but headless and CSV-based.

- `--gate-review auto` (default): one pass — compute smart proposals, apply them, AND write the
  template + figures so you can audit after the fact.
- `--gate-review propose`: **PASS 1** — write `gating_thresholds_template.csv` + `figures/gate_*.png`,
  then **stop without writing an SCE**. Review the figures, edit `final_cutoff` / `apply`.
- `--gate-review apply --thresholds <edited.csv>`: **PASS 2** — apply the edited template.

`--threshold-scope pooled` harmonizes each 1D gate to the across-sample **median** cutoff
(broadcast as `sample_id=ALL`) then re-applies — use when per-sample staining is stable and you want
one common boundary; the default `per_sample` is correct when staining varies between samples.

### Template schema (`gating_thresholds_template.csv`; see `assets/gating_thresholds_template_example.csv`)

`sample_id, gate, channel_x, channel_y, method, proposed_cutoff, direction, pct_removed,
valley_confidence, unimodal, status, final_cutoff, apply, notes`

- **Edit only** `final_cutoff` (the value applied) and `apply` (`Y`/`N`). Everything else is a
  read-out of how the proposal was derived, kept for provenance.
- `sample_id=ALL` broadcasts a row to every sample (convenient for a single reviewed cutoff).
- `status` values: `auto_ok`, `control`, `percentile`, `fixed`, `pooled`, `user_edit`, and the
  review flags `REVIEW_unimodal` / `REVIEW_shallow` / `REVIEW_too_few` / `REVIEW_gmm` /
  `REVIEW_otsu` / `REVIEW_no_control`. **Any `REVIEW_*` means: look at the figure before trusting it.**

---

## How this composes with the gate-gently rule

Data-driven does **not** mean aggressive. Scatter/singlet gates still default to permissive
(MAD bands, low debris floor), the per-gate >20% WARNING and the >30% OVER-GATING ALARM are
unchanged, and when in doubt the engine chooses the *conservative* percentile and flags for review
rather than cutting harder. Everything in `qc_gating.md` about inspecting the scatter gate first and
reconciling against a manual export (`08_validate_vs_manual.R`) still applies.

---

## References
- Malek M, Taghiyar MJ, Chong L, et al. flowDensity: reproducing manual gating of flow cytometry data
  by automated density-based cell population identification. *Bioinformatics.* 2015;31(4):606-607.
  doi:10.1093/bioinformatics/btu677.
- Mair F, Hartmann FJ, Mrdjen D, et al. The end of gating? An introduction to automated analysis of
  high dimensional cytometry data. *Eur J Immunol.* 2016;46(1):34-43. (flowDensity workload reduction.)
- Liu P, Pan Y, Chang HC, et al. Comprehensive evaluation and practical guideline of gating methods for
  high-dimensional cytometry data: manual gating, unsupervised clustering, and auto-gating.
  *Briefings in Bioinformatics.* 2024;26(1). doi:10.1093/bib/bbae633.
- Lee H, Sun Y, Patti-Diaz L, et al. High-Throughput Analysis of Clinical Flow Cytometry Data by
  Automated Gating. *Bioinform Biol Insights.* 2019;13. doi:10.1177/1177932219838851.
- Petrunkina A, Harrison R. Mathematical analysis of mis-estimation of cell subsets in flow cytometry:
  Viability staining revisited. *Journal of Immunological Methods.* 2011;368(1-2):71-79.
  doi:10.1016/j.jim.2011.02.009.
- Lo K, Hahne F, Brinkman RR, Gottardo R. flowClust: a Bioconductor package for automated gating of
  flow cytometry data. *BMC Bioinformatics.* 2009;10:145. doi:10.1186/1471-2105-10-145.
- Lo K, Brinkman RR, Gottardo R. Automated gating of flow cytometry data via robust model-based
  clustering. *Cytometry Pt A.* 2008;73A(4):321-332. doi:10.1002/cyto.a.20531.
- Finak G, Frelinger J, Jiang W, et al. OpenCyto: An Open Source Infrastructure for Scalable,
  Robust, Reproducible, and Automated, End-to-End Flow Cytometry Data Analysis. *PLoS Comput Biol.*
  2014;10(8):e1003806. doi:10.1371/journal.pcbi.1003806.
- Maecker HT, Trotter J. Flow cytometry controls, instrument setup, and the determination of
  positivity. *Cytometry Pt A.* 2006;69A(9):1037-1042. doi:10.1002/cyto.a.20333.
- Otsu N. A threshold selection method from gray-level histograms. *IEEE Trans Syst Man Cybern.*
  1979;9(1):62-66.
- Hartigan JA, Hartigan PM. The dip test of unimodality. *Ann Stat.* 1985;13(1):70-84.

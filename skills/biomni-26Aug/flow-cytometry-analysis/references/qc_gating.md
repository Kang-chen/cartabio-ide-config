# Quality control gating (first-class, modality-specific)

**Why this exists.** In the reference worked example, QC removed *zero* cells — not because the data
were pristine by luck, but because the benchmark dataset (HDCytoData) was **pre-cleaned upstream**.
Real FCS files are not. Skipping QC on real data lets debris, dead cells, doublets, and beads leak
into clustering, producing spurious "populations" (e.g. platelet CD41/CD61 or erythroid CD235ab
doublet clusters that are really contamination). QC is a first-class step, not an afterthought.

## THE GOLDEN RULE: gate GENTLY, and prove you didn't over-gate

**Over-gating is the dominant, silent failure mode of automated cytometry.** A too-tight scatter or
singlet gate does not error — it quietly discards *real* single cells, and because the discarded
cells are not a random sample (they skew toward one scatter region, i.e. often toward specific
lineages or toward brighter/dying cells), it **biases every downstream number**: cluster abundances,
population frequencies, and any viability/death readout. It looks fine. It is not.

Real-world example this rule is built on (ADCC assay): a scatter/singlet gate using an
`FSC-H/FSC-A` ratio window of **0.75–1.25** with tight `FSC 10k–250k / SSC 5k–250k` bounds discarded
~30% of real single cells, and did so *disproportionately* for the target-cell population — so the
downstream target gate captured only **895 of an expected ~7,600** cells. Because that surviving
subset skewed toward brighter/dying cells, the apparent baseline death rate was inflated **~4–5×**
(≈40–50% vs a true ≈10%). Loosening the gate to a gentle `FSC-H/FSC-A` ratio of **0.6–1.4** with
relaxed floors (`FSC>3000, SSC>1000`) reproduced the manual result to within ~1 percentage point —
**with the original viability threshold unchanged.** The scatter gate was the culprit; the viability
cutoff never was.

Therefore:
- **Default to permissive gates.** Debris floor = a low percentile (≈2nd). Singlet band =
  median ± **4·MAD** (gentle), not ±3 and never a hardcoded ratio window like 0.75–1.25. All
  thresholds are exposed (`--singlet-mad-k`, `--debris-pct`) and should be loosened, not tightened,
  when in doubt.
- **Alarm on over-removal, not just under-removal.** `01_load_and_qc.R` logs in→out counts for every
  gate, WARNs when any single scatter/singlet gate removes >20% of entering events, and raises a loud
  **OVER-GATING ALARM** when scatter+singlet gates jointly remove >30% (`--overgate-alarm`). Treat the
  alarm as a stop-and-review, not a log line to scroll past.
- **The alarm keys on the fraction of *total* events removed** — so a gate that disproportionately
  clips a *minority* population (e.g. target cells that are only ~30% of events) can badly bias that
  population without the total removal reaching 30%. The per-gate >20% WARNING and, above all, the
  **manual-gate reconciliation** (`08_validate_vs_manual.R`, which compares per-sample cell/target
  counts directly) are the sensitive backstops for that case. Do not treat a quiet total-removal
  number as proof the target population survived intact.

## Flow (fluorescence) QC, in order

Cutoffs are **data-driven** (density valley), **reviewable** (editable template + per-gate figures),
and **multivariate** (2D joint gates) — see `references/threshold_selection.md`. The percentiles/MAD
bands below are the **honest fallbacks** (used when a channel is unimodal or its valley is too
shallow), not the primary method.

1. **Debris removal** — 2D **FSC-A × SSC-A** joint gate (robust MCD ellipse / flowClust) keeps the
   main cell cloud when `--multivariate on` (default); otherwise two 1D low-scatter floors. Fallback
   floor = 2nd percentile of each (`--debris-pct`). Gentle by design.
2. **Doublet removal** — singlets have consistent `FSC-A / FSC-H` (or `FSC-A` vs `FSC-W`). Keep
   events whose ratio is within median ± **4·MAD** (permissive default; `--singlet-mad-k`), written as
   two editable rows (`singlet_low`/`singlet_high`). Do **not** substitute a fixed ratio window
   (e.g. 0.75–1.25) — those clip the diagonal singlet cloud and preferentially remove real cells.
3. **Live/dead** — if a viability channel exists (`Live|Dead|Viability|Zombie|7AAD|DAPI|PI`), place
   the cutoff at the **density valley** between live (dim) and dead (bright), or **anchor it to an
   unstained/FMO control** when supplied (`--controls`). If the channel is unimodal (Hartigan dip) or
   the valley is too shallow, fall back to the 95th percentile and flag **REVIEW** (never invent a
   boundary). A 2D viability × scatter diagnostic is also emitted. **This is not a scatter gate** — do
   not tighten it to fix a discrepancy that a scatter gate caused (see below). The live/dead cutoff is
   the highest-leverage threshold in the whole pipeline — get it right and show it.
   A **viability × lineage-marker diagnostic** (`gate_<sample>_live_dead_lineage_<marker>.png`)
   is also emitted for each marker resolved by `--lineage-markers` (default
   auto-detect: `CD15,CD66b,CD16,CD11b`). **Rationale:** neutrophils take up viability dye without
   being dead, so a viability × scatter view alone can misgate live neutrophils as dead; viability ×
   a granulocyte lineage marker makes the dye-bright-but-live population obvious. Diagnostic only
   (`apply=N`); decoupled from `--multivariate`. If no lineage marker is in the panel, it is skipped
   gracefully (no error).

## Mass cytometry (CyTOF) QC, in order

As with flow, cutoffs are data-driven (valley) with percentile fallback + a review template; the
percentiles below are fallbacks. Order below matches the script. See `references/threshold_selection.md`.

1. **DNA / intact cells** — gate on DNA intercalators `Ir191`/`Ir193`: a **valley** low-bound
   (`dna_intact_low`, keep-above) to exclude debris, plus a 99.5th-percentile high-bound
   (`dna_intact_high`, keep-below) to trim high-DNA doublets/aggregates.
2. **Gaussian-parameter doublets** — `Event_length` within median ± **4·MAD** (permissive default;
   `--singlet-mad-k`), written as an editable low/high band; Gaussian residual/center/width when present.
3. **Viability** — cisplatin `Pt195` (or `Pt194`): **valley** between live and cisplatin-bright dead
   (control-anchored if supplied), 95th-percentile fallback; a 2D **Pt × DNA** diagnostic is also
   emitted. A **Pt viability × lineage-marker diagnostic** (`live_dead_lineage_<marker>`)
   is also emitted for each marker resolved by `--lineage-markers` (default auto-detect
   `CD15,CD66b,CD16,CD11b`) — same neutrophil rationale as the flow branch (dye-bright-but-live
   granulocytes). Diagnostic only (`apply=N`); skipped gracefully if no lineage marker is present.
4. **Bead removal / normalization** — if bead channels exist (`Ce140|EQ|bead`), remove beads
   (**valley**, or 99th-percentile fallback) and prefer `CATALYST::normCytof()` for signal
   normalization when bead channels are present.

## Universal

- **Always drop non-finite** values (NaN/Inf) introduced by transformation or compensation.
- Every gate logs: channel used, threshold, cells in → cells out, % removed.
- If a gate's channel is absent, **log that it was skipped** — silence hides missing QC.
- **Every gate is data-driven, logged, figured, and overridable:** cutoffs come from density valleys
  (or unstained/FMO controls) with an honest percentile fallback, each gate writes a diagnostic figure
  (`gate_<sample>_<gate>.png`), and every cutoff is an editable row in
  `gating_thresholds_template.csv` (two-pass: `--gate-review propose` → edit → `--gate-review apply`).
  See `references/threshold_selection.md`.

## When automated results disagree with manual gating: inspect the SCATTER gate FIRST

If your automated numbers diverge from a scientist's manual gate (in FlowJo or elsewhere), resist the
reflex to blame the marker/viability threshold. The empirically correct debugging order is:

1. **Scatter/singlet gate** — compare your kept-cell count per sample to the manual "Cells"/"Single
   cells" count. A large deficit here is the usual culprit and explains most downstream discrepancies.
2. **Target/marker-positive gate** — only after (1) is ruled out. Losing the target population is
   almost always an *upstream* scatter problem, not the marker cutoff itself.
3. **Viability/DEAD threshold** — last. In the ADCC case above, the agent initially misdiagnosed the
   viability threshold as the problem and even tried to "recalibrate" it; the actual fix was upstream,
   and the original viability cutoff turned out to be correct all along.

## Implausibility trip-wires (sanity checks that trigger a gating REVIEW, not a rationalization)

A result that is internally consistent can still be wrong. Treat the following as **stop-and-review**
signals — do **not** accept them by inventing a biological story:

- **Implausibly high QC removal** — scatter+singlet removing >30% of a raw acquisition (the alarm).
- **Implausible control/baseline readouts** — e.g. an ADCC/CDC no-antibody (0-dose) control showing
  **>25–30% dead** target cells. Do not default-explain this as "antibody-independent NK killing" or
  similar; a high baseline is far more often a gating artifact. Rule out the scatter gate first.
- **A readout that co-varies with the wrong variable** — e.g. per-well target-cell *counts* that
  track dose/killing rather than staying roughly uniform across replicate wells. That pattern is a
  gating artifact signature, not biology (see the skepticism note below).

## Skepticism about self-generated diagnostics (seek external ground truth)

Bimodal histograms, a monotonic dose-response, and plausible-looking internal correlations are **not**
proof that gating is correct — they are exactly what a biased gate can also produce. In the ADCC run,
the pipeline's own diagnostics all looked self-consistent, and the agent concluded the gate was fine;
only an external **manual-gating export** revealed the true cause. Whenever any independent ground
truth exists (a manual FlowJo statistics export, spike-in controls, known seeding counts), **use it to
validate before trusting downstream fits** — see `references/validation_vs_manual.md` and
`scripts/08_validate_vs_manual.R`. Do not treat "my numbers are self-consistent" as validation.

## The honesty rule

If QC removes 0 cells, say so *and* say why you believe it (e.g. "input appears pre-gated"). A 0%
removal on raw instrument output is a red flag, not a success. **Symmetrically, a HIGH removal (>30%
scatter/singlet) is an equal-or-worse red flag** — surface it, don't bury it.

## References
- Bagwell et al. and standard flow QC practice (FSC/SSC debris + doublet discrimination).
- Finck et al., Normalization of mass cytometry data with bead standards. Cytometry A 2013.
- Crowell et al., CATALYST (`normCytof`, `compCytof`).
- **`references/threshold_selection.md`** — data-driven cutoff methods (valley/GMM/Otsu/control), the
  unimodality honesty guard, 2D joint gates, and the editable-template review workflow, with primary
  citations (flowDensity/deGate, flowClust, OpenCyto, FMO-control gating, Petrunkina & Harrison).

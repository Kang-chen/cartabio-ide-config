# Modality viability scorecard — rubric

The scorecard turns the three evidence streams into a transparent, reproducible **triage verdict**
for each modality. It is a heuristic to rank modalities, **not** a prediction of clinical success.
Always show the rubric and the inputs so the reader can audit the call.

## Dimensions (scored per modality, 0–3)

`0 = None · 1 = Low · 2 = Medium · 3 = High`

### 1. Tractability (from Open Targets buckets for that modality)
- **3 (High):** a clinical-precedent bucket is True (Approved Drug / Advanced Clinical / Phase 1).
- **2 (Medium):** no clinical bucket, but multiple enabling buckets True (e.g. structure+ligand for
  SM; ubiquitination + small-molecule-binder for PR; surface localization for AB).
- **1 (Low):** only a single weak/localization/family bucket True.
- **0 (None):** all buckets False, **or** a modality-specific deal-breaker (for AB: intracellular-
  only localization with no accessibility bucket → force to 0 regardless of other flags).

### 2. Structural pocket support (from fpocket)
Applies mainly to modalities that need a small-molecule binding site (SM, PR). For AB it reflects
epitope accessibility, not a pocket, so score AB structurally on **accessibility**, not pocket depth.
- **3 (High):** top pocket druggability > 0.5 in a relevant (ideally holo/engaged) conformation.
- **2 (Medium):** borderline pocket 0.2–0.5, or a druggable pocket only in a predicted/AlphaFold model.
- **1 (Low):** pocket < 0.2 (shallow surface) but some cavity exists.
- **0 (None):** a structure was analysed but no usable pocket was found (for AB: not surface-accessible).
- **NA (not assessed):** no structure was retrieved (no experimental PDB and no AlphaFold fallback).
  NA is **excluded** from the Overall mean — it is missing data, not negative evidence.

### 3. Clinical precedent (from `drugAndClinicalCandidates` + literature)
- **3 (High):** ≥1 approved drug of that modality against the target.
- **2 (Medium):** clinical-stage candidate (Phase 1–3) of that modality, not yet approved.
- **1 (Low):** only preclinical / literature reports of that modality.
- **0 (None):** no drug/candidate of that modality.

## Overall score
`Overall = round(mean(assessed dimensions))`, then clamp to 0–3. Dimensions scored **NA**
(not assessed) are excluded from both the numerator and the denominator.
Break ties toward the modality with **clinical precedent** (dimension 3), because a validated modality
is a stronger triage bet than one strong only on in-principle enablement.

Report the Overall as the verdict word:
`0 → Not viable · 1 → Low · 2 → Medium / emerging · 3 → High / most viable`.

## Naming the winner(s)
- **Most viable modality** = highest Overall (ties broken by Clinical precedent, then Tractability).
- **Frontier / emerging modality** = the next-highest Overall that is driven by *enabling* evidence
  rather than approvals (call this out explicitly — it's the "where the field is heading" signal,
  e.g. degraders for a target with only SM approvals today).
- **Ruled-out modality** = any Overall 0; state the concrete reason (e.g. "intracellular →
  antibodies not viable").

## Worked example (KRAS, from the original run — for calibration only)
| Modality | Tractability | Structural | Clinical | Overall | Verdict |
|---|---|---|---|---|---|
| Small molecule | 3 | 3 (SII-P 0.993 holo) | 3 (sotorasib, adagrasib approved) | 3 | Most viable |
| PROTAC/degrader | 3 (5 enabling buckets) | 2 (uses SM handle) | 1 (LC-2/ACBI4 preclinical) | 2 | Emerging frontier |
| Antibody | 1 (localization only) | 0 (intracellular) | 0 | 0 | Not viable |

> These KRAS numbers are illustrative anchors, **not** defaults. Recompute every dimension from the
> live data for the actual target being assessed.

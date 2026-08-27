# Validation design

## Contents

- Selection and assessment boundary
- Outer split choice
- Inner validation
- Calibration and uncertainty
- Applicability domain
- Censored endpoints

## Selection and assessment boundary

Lock the outer partition before model or representation comparison. Use only outer-training
data for candidate selection, probability calibration, conformal calibration, and decision
thresholds. Evaluate the chosen procedure once on the outer test set. After assessment, fit a
deployment bundle on all currently available labels; keep the assessment unchanged.

## Outer split choice

Choose the split that matches the deployment question:

1. **Time:** prefer when complete dates exist. It estimates performance on later, previously
   unseen molecular identities and purges later retests.
2. **Deployment/MOOD:** use when the actual prospective structures are available. Splito ranks
   candidate splits by how well their test-to-train distance distribution matches deployment.
3. **Scaffold:** use for unseen chemotypes when dates and deployment structures are unavailable.
4. **Cluster:** use when Murcko scaffolds are uninformative or chemistry is largely acyclic.
5. **Random:** use only for an explicitly interpolation-like deployment question.

If a split produces too few samples or only one classification class, stop. Do not silently
fall back to random.

## Inner validation

Run grouped folds inside outer training. Use chronological folds for temporal evaluation and
scaffold groups otherwise, with fingerprint clusters as a fallback when all Murcko scaffolds
are empty. Compare a deliberately small ladder:

- dummy baseline;
- true binary Morgan/Tanimoto 1-NN;
- ridge or logistic regression;
- XGBoost;
- bound-aware constant and XGBoost AFT for censored regression.

Use MAE for uncensored regression, average precision for classification, and interval C-index
for censored regression. Apply the one-standard-error rule and prefer the simpler candidate
when differences are not resolved by inner folds.

## Calibration and uncertainty

Reserve a group-disjoint calibration slice within outer training. MAPIE supplies absolute-
residual regression intervals and LAC classification sets. Report empirical locked-test
coverage and interval width/set size.

For temporal or scaffold shift, conformal exchangeability is weakened. Coverage below nominal
is evidence of shift; do not relabel it as a formal guarantee. The deployment bundle uses
cross-conformal fitting on the full labelled dataset, but its prospective credibility remains
bounded by the locked outer assessment.

The runtime does not report conformal intervals for censored AFT models. Exact-observation-only
residual intervals would not justify a general censored-label coverage claim.

## Applicability domain

Compute nearest-neighbour similarity with a dedicated Morgan radius-2 fingerprint, independent
of the selected model representation. With at least 30 training molecules, use the fifth
percentile of leave-one-out training similarity as a data-derived support threshold; otherwise
use a clearly labelled heuristic. Report training out-of-fold error by similarity bin, and the
computed `error_monotonicity` verdict (Spearman rho of per-stratum error vs ascending similarity):
the domain flag is a trust signal only when error falls as similarity rises. Treat a
`not_evidenced` or `inverted` verdict as evidence the flag does not track error on this dataset.

The AD threshold and the locked-test AD flags use the outer-training molecules as their
similarity reference, so the locked assessment stays free of test information (recorded as
`ad_reference_set_evaluation`). The deployment bundle uses a deliberately broader reference — all
audited molecules (train+test) — when flagging new molecules at scoring time (recorded as
`ad_reference_set_deployment`); that broader support set is never used in the locked assessment.
Derive each reference set's description, including its count, from the array actually passed to
the similarity call rather than from a hand-written constant.

An in-domain flag is not proof of accuracy. An out-of-domain flag means the prediction is an
extrapolative hypothesis that needs experimental confirmation.

## Censored endpoints

Train only bound-aware candidates when any observation is censored. Evaluate ordering only for
pairs whose truth intervals establish an order. Report interval C-index for all usable pairs
and MAE/RMSE only on exact observations. Always report exact and censored counts beside them.


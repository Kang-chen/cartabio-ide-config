# Assay and endpoint schema

## Contents

- Required scientific declarations
- Regression labels and censoring
- Classification labels
- Replicates and assay identity
- Endpoint-specific framing

## Required scientific declarations

Declare the structure column, endpoint column, task, reporting scale, and unit. Declare every
available column that changes what a measurement means: assay/protocol ID, species, tissue or
matrix, pH, incubation time, directionality, laboratory, and batch. The audit treats distinct
combinations as distinct assay signatures and blocks mixed signatures by default.

Do not merge sources solely because both columns are called `solubility`, `CLint`, or `Papp`.
Document unit conversion and assay harmonization upstream. Preserve a source/assay identifier
even after harmonization.

## Regression labels and censoring

Use `scale: linear` when labels are reported on their physical scale. Use `scale: log10` only
when values and censor limits are already log10 transformed. Qualified values can be embedded
(`>300`, `<=1.2`) or stored in `qualifier_column`.

The runtime represents every regression label as `[lower_bound, upper_bound]`:

| Source | Interval |
|---|---|
| `4.2` | `[4.2, 4.2]` |
| `>4.2` | `[4.2, +∞]` |
| `<4.2` | `[-∞, 4.2]` |

Never convert a limit to an exact value. XGBoost AFT requires a positive physical response.
For a log10-transformed positive endpoint, the runtime exponentiates interval bounds for AFT
and converts predictions back to the reporting scale.

## Classification labels

Prefer regression when an underlying continuous measurement is available. For a truly binary
endpoint, map two source values explicitly to `0` and `1` and name the positive class. Do not
infer meaning from alphabetical order or prevalence. Qualified/censored class labels are not
supported.

The default probability threshold is `0.5`; changing it is a decision-policy choice, not a way
to improve a locked test result. Select a different threshold using training-only decision
costs and record the choice.

## Replicates and assay identity

Standardize structures first, then identify a molecule by parent InChIKey plus assay signature.
Assign the outer split before aggregating replicates. For temporal evaluation:

- train only on measurements before the cutoff;
- test only later identities unseen before the cutoff;
- purge later retests of previously observed identities.

Aggregate exact regression replicates by their median and retain their standard deviation as a
noise estimate. Combine purely censored replicates by interval intersection. Block exact class
conflicts and incompatible censor intervals.

## Endpoint-specific framing

- Solubility, clearance, concentration, permeability, and potency are commonly positive and
  right-skewed; log10 reporting is often appropriate after confirming the source convention.
- Fractions such as unbound fraction need a declared bounded transformation if transformed.
- Permeability must retain direction, cell system, pH, and unit.
- Clearance must retain species, matrix/system, normalization basis, and unit.
- Binding and inhibition calls must retain concentration, protocol, and positive-class meaning.


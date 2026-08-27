# Transformation & compensation (modality-aware, never silent)

The single most consequential preprocessing choice in cytometry is how raw intensities are
transformed. Getting it wrong distorts every downstream cluster. **Auto-detect the modality, but
never auto-decide silently** — log exactly what was applied and expose overrides.

## Detect modality from FCS keywords (not from the filename)

- **Mass cytometry (CyTOF)** if `$CYT`/`$CYTSN` matches `CYTOF|HELIOS|MASS|DVS|FLUIDIGM`, OR if
  >30% of channels look like mass tags via regex
  `Di$|_Di|[0-9]{2,3}(Nd|Sm|Eu|Gd|Tb|Dy|Ho|Er|Tm|Yb|Lu|La|Ce|Pr|Pt|Ir|Rh|In|Cd|Xe|Ba|Te|Sn|Y|Cs)`.
- **Flow (fluorescence)** if FSC/SSC scatter channels are present, or instrument keywords match
  known flow cytometers.
- **Spectral instrument** (hint, not a refusal trigger) if `$CYT`/`$CYTSN` matches
  `AURORA|CYTEK|SPECTRAL|SP6800|ID7000`. The instrument name alone never causes an abort; the
  raw-vs-unmixed decision is made from channel/marker structure:
  - **Already-unmixed** (proceed as fluorescence flow) when channels carry named fluorophores /
    antibody markers (e.g. `PerCP-A`, `FITC-A`, `PE-Cy7-A`, `CD3`, `CD45`) — the standard
    analysis-ready output of Cytek Aurora / Sony ID7000 workflows.
  - **Raw spectral** (unmixing pending → refuse) when channels are raw unnamed detectors matching
    real instrument conventions: Cytek Aurora `^(UV|V|B|YG|R)[0-9]+-?A?$`
    (`UV1-A`–`UV16-A`, `V1-A`–`V16-A`, `B1-A`–`B14-A`, `YG1-A`–`YG10-A`, `R1-A`–`R8-A`) or Sony
    SP6800/ID7000 `^(CH|FL)?[0-9]+-?A?$` (32–184 numbered channels) with no fluor/marker names.
  - Ambiguous spectral files **fail open** as unmixed (logged warning); force refusal with
    `--spectral-state raw`.

## Transform by modality

| Modality | Default transform | Rationale |
|---|---|---|
| CyTOF | `arcsinh(x / 5)` (cofactor **5**) | Standard for mass cytometry; 5 is the community default and matches CATALYST. |
| Flow | **logicle/biexponential** (`flowCore::estimateLogicle`, per-channel) OR estimated arcsinh cofactor | Fluorescence has negative values from compensation; a fixed cofactor 150 is a *fallback*, not a default. |
| Spectral (already unmixed) | **logicle/biexponential** or estimated arcsinh (as flow) | Unmixed spectral IS fluorescence flow; apply embedded spillover compensation if present, else warn-and-proceed. |
| Spectral (raw, unmixing pending) | **REFUSE** | Needs validated upstream spectral unmixing; override with `--spectral-state unmixed` if already done. |

**Do NOT hardcode cofactor 150 for flow.** If per-channel logicle is unavailable, estimate an
arcsinh cofactor per channel — e.g. `est_arcsinh_cofactor = max(1, median over channels of the
20th percentile of |x|)` — and log the value. Cofactor 150 is only ever an *explicit* fallback the
user opted into.

## Compensation (fluorescence flow only)

- If the FCS has an embedded spillover matrix (`flowCore::spillover(ff)` returns one of
  `$SPILL`/`$spillover`/`$`), apply it with `flowCore::compensate()` **before** transformation.
- If no spillover matrix is embedded, **warn loudly** and proceed uncompensated (or accept a
  user-supplied matrix). Never silently skip.
- CyTOF has minimal spillover; CATALYST offers `compCytof()` if a spillover matrix is supplied, but
  compensation is usually not applied by default.

## What to log (every run)

The pipeline writes `qc_transform_log.txt` recording: detected modality + the evidence;
transform chosen + cofactor/logicle params; whether compensation was applied and its source;
the spectral decision (raw vs unmixed, with reason); and any refusal (raw spectral). If a reader cannot reconstruct the preprocessing from the log, the log
is incomplete.

## Overrides

Every automatic decision is overridable via CLI flags on `01_load_and_qc.R`
(`--modality`, `--spectral-state`, `--transform`, `--cofactor`, `--compensate`). An explicit
setting is **never silently overridden** by auto-detection — the override wins and is logged.
`--spectral-state {auto|raw|unmixed}` controls the raw-vs-unmixed spectral decision: `auto`
classifies from channel/marker structure; `raw` forces refusal; `unmixed` forces processing as
fluorescence flow. Auto-detection is a convenience, not a constraint.

## References
- Nowicka et al., CyTOF workflow, F1000Research 2019;6:748 (arcsinh cofactor 5 for CyTOF).
- Parks, Roederer, Moore. A new "Logicle" display method. Cytometry A 2006 (logicle for fluorescence).

# Schema Contract — input data items & validation rules

The skill accepts **tidy long-format** concentration-time (± response) data using NONMEM-style
data items. The golden rule: **validate loudly, never drop rows silently.** A silent-drop bug is
the classic way a pharmacometric analysis becomes quietly wrong (e.g. dropping all PD rows and
"successfully" fitting PK-only without telling anyone).

---

## Canonical data items

| Item | Meaning | Required | Type / rules |
|------|---------|----------|--------------|
| `ID` | Subject identifier | **yes** | any; treated as grouping factor |
| `TIME` | Time since first dose | **yes** | numeric hours; non-negative; sorted within `ID` |
| `DV` | Dependent variable (observation) | **yes** | numeric; concentration and/or response |
| `AMT` | Dose amount | **yes** (≥1 dose row) | numeric; `0`/`NA` on observation rows |
| `EVID` | Event ID | recommended | `0` = observation, `1` = dose; **non-standard codes (e.g. NONMEM `101`, `4`) are normalized to dose if `EVID≠0`**; derived from `AMT>0` if absent |
| `CMT` | Compartment | needed for multi-endpoint | on **obs** rows must equal the **endpoint name** (see troubleshooting #3) |
| `DVID` | DV type label | needed for PK+PD | distinguishes endpoints, e.g. `cp`/`conc` vs `pca`/`effect` |
| `MDV` | Missing DV flag | optional | `1` = ignore this row |
| `WT` | Body weight (kg) | optional | enables allometric scaling |
| `AGE` | Age (y) | optional | covariate testing |
| `SEX` | Sex | optional | encode numerically (e.g. male=1, female=0) and record the mapping |
| other | Any covariate | optional | keep, document units |

---

## Column mapping

User columns rarely match the canonical names. Map case-insensitively and by common synonyms,
then **print the mapping**:
- `ID` ← `id`, `subject`, `subj`, `usubjid`
- `TIME` ← `time`, `t`, `tad` (flag if "time after dose" vs "time after first dose")
- `DV` ← `dv`, `conc`, `concentration`, `y`, `obs`, `value`
- `AMT` ← `amt`, `dose`, `dosemg`
- `EVID` ← `evid`, `event`
- `CMT` ← `cmt`, `compartment`
- `DVID` ← `dvid`, `endpoint`, `analyte`, `ytype`
- covariates ← `wt`/`weight`/`bw`, `age`, `sex`/`gender`

If a required item cannot be mapped or derived, **stop with a clear error** naming the missing
item and the columns that were seen.

---

## Validation summary (print AND embed in report Methods)

Compute and report:
1. **Counts:** n subjects, n rows total, n dose events, n observations **per endpoint** (split by
   `DVID`), n covariate values present.
2. **Data-quality flags:** rows with missing `DV`, `DV ≤ 0` (flag; log-scale models can't use
   non-positive obs), duplicated `(ID, TIME, DVID)`, negative/decreasing `TIME` within `ID`,
   subjects with **no dose** or **no observations**.
3. **Exclusions:** if any rows are excluded (e.g. `MDV==1`, missing DV), **list the count and the
   exact reason**. Never exclude without reporting.
4. **Endpoint detection:** determine whether a distinct **response** endpoint exists beyond
   concentration. If not → set `PK_ONLY = TRUE` (skip PD/dose stages, still report).
5. **Dose design note:** report the distinct dose levels and whether the design is single-dose /
   single-level (→ dose recommendations are extrapolation) vs multi-level.

---

## Endpoint / compartment convention (critical for PK+PD)

For multi-endpoint fits, the observation `CMT` **must be the model endpoint name**, not the ODE
compartment (see `troubleshooting.md` #3):
```
dose rows:   CMT = "depot"           EVID = 1
PK obs:      CMT = "cp"  (endpoint)  EVID = 0   DVID = concentration label
PD obs:      CMT = "pca" (endpoint)  EVID = 0   DVID = response label
```

---

## Public example datasets (via `nlmixr2data`)

| Dataset | Load | Endpoints | Design | Use |
|---------|------|-----------|--------|-----|
| Theophylline | `data(theo_sd)` (nlmixr2data) or `Theoph` (datasets) | PK only | single oral dose, 12 subjects, ~132 obs | **PK-only** demo / smoke test |
| Warfarin | `data(warfarin)` (nlmixr2data) | PK (`cp`) + PD (`pca`) | single 1.5 mg/kg dose, 32 subjects | PK/PD + indirect-response demo |

Example mapping for `warfarin` (columns `id,time,amt,dvid,dv,wt,age,sex`):
```r
df$WT  <- df$wt; df$AGE <- df$age; df$SEX <- ifelse(df$sex=="male",1,0)
df$EVID <- ifelse(df$amt > 0 & (is.na(df$dvid) | df$dvid==""), 1, 0)
# dose rows -> CMT "depot"; dvid=="cp" obs -> CMT "cp"; dvid=="pca" obs -> CMT "pca"
```
Note: warfarin subject numbering skips one ID by design — that is expected, not a data error.

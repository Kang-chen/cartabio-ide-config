# nlmixr2 / rxode2 Troubleshooting — verified gotchas

These are real failure modes encountered building a warfarin popPK/PD model with this exact
stack (rxode2 5.1.2, nlmixr2est 6.0.1, nlmixr2 5.0.0, R 4.4.2). Check here **before** debugging
blindly — most of these produce cryptic errors with non-obvious causes.

---

## 1. `cp` is a reserved name in rxode2 → rename the concentration output

**Symptom:** `rxSolve` / `rxSolveSEXP` errors such as *"parameter required"* or a model that
silently fails to map the concentration variable when you name your central-compartment
concentration `cp`.

**Cause:** `cp` is reserved inside rxode2 model code.

**Fix:** name the derived concentration something else — use **`conc`**:
```r
sim_mod <- rxode2({
  d/dt(depot)  <- -KA*depot
  d/dt(center) <-  KA*depot - (CLs/Vs)*center
  conc <- center/Vs           # NOT 'cp'
  ...
})
```
(Inside an nlmixr2 `model({...})` block the endpoint variable `cp` is fine as the *observed*
endpoint name — the reserved-name clash bites specifically in **rxode2 simulation models**.)

---

## 2. `parFixedDf` / `parFixed` return NAMED numerics → wrap in `as.numeric()`

**Symptom:** After building a parameter vector from a fit, rxode2 complains a parameter is
missing/required, and inspection shows param names like **`KA.tka`**, `CLs.tcl` instead of
`KA`, `CLs`.

**Cause:** `fit$parFixedDf["tka","Estimate"]` returns a **named** numeric (the row name tags
along). `c(KA = that)` becomes `KA.tka`.

**Fix:** strip names with `as.numeric()` on every extraction:
```r
pk  <- fit_pk$parFixedDf
ka0 <- as.numeric(exp(pk["tka","Estimate"]))
cl0 <- as.numeric(exp(pk["tcl","Estimate"]))
v0  <- as.numeric(exp(pk["tv" ,"Estimate"]))
p   <- c(KA = ka0, CLs = cl0, Vs = v0)   # now clean names
```

---

## 3. Observation `CMT` must be the ENDPOINT name, not the internal compartment

**Symptom:** `nlmixr2` errors during setup with something like
*"'dvid'->'cmt' ... 'cmt' on observation record ... undefined compartment"*, or the
DVID→modeledCmt translation table looks wrong.

**Cause:** For multi-endpoint (PK+PD) data, the `CMT` on an **observation** row must equal the
**model endpoint name** (the left-hand side of the `~` residual line), not the internal ODE
compartment. E.g. use `cp` (the endpoint), **not** `center` (the compartment).

**Fix:** map observation compartments to endpoint names:
```r
# dose rows:      CMT = "depot"
# PK obs rows:    CMT = "cp"     (endpoint name, NOT "center")
# PD obs rows:    CMT = "pca"    (endpoint name)
```

---

## 4. `false convergence (8)` → refit with bobyqa outer optimizer

**Symptom:** FOCEi ends with *"false convergence (8)"* (the default `nlminb` outer optimizer).

**Cause:** A numerical artifact of the optimizer near a flat/awkward region — usually **not** a
wrong model; parameters are often unchanged.

**Fix:** refit with the derivative-free BOBYQA outer optimizer:
```r
fit <- nlmixr2(model, data, est = "focei",
               control = foceiControl(print = 0, outerOpt = "bobyqa"))
# -> "Normal exit"
```
Confirm the estimates match the pre-refit values before trusting them.

---

## 5. Boundary / unidentifiable `Imax` (or `Emax`) → fix it at 1 when justified

**Symptom:** An inhibitory/stimulatory maximum runs to the boundary — e.g. `expit(56.266) = 1.0`
— and the **condition number explodes** (warfarin: cond# ≈ 21,164), often with a failed or
untrustworthy covariance step.

**Cause:** The data don't contain enough high-concentration information to identify the maximum
separately from the potency term; the model wants `Imax → 1`.

**Fix:** fix the maximum at 1 (complete inhibition/stimulation) when mechanistically justified:
```r
imax <- 1                       # fixed, not estimated
kin  <- kout * base
d/dt(pca) <- kin*(1 - imax*conc/(ic50 + conc)) - kout*pca
```
Result in the warfarin case: an essentially identical fit with the **condition number dropping
to ~40** and a clean full covariance step. Report `Imax = 1 (fixed)`.

---

## 6. Allometric covariates in rxode2 simulation → bake scaling into per-scenario params

**Symptom:** Covariate columns behave unexpectedly in simulation; `WT` seems missing or
collides.

**Cause:** rxode2 **lowercases** covariate column names (`WT` → `wt`), which can collide with
parameters and confuse per-scenario scaling.

**Fix:** compute the allometrically-scaled disposition parameters **outside** the model and pass
them as scenario parameters, rather than carrying a covariate column:
```r
CLs <- cl0 * (WT/70)^0.75      # bake weight scaling into the parameter
Vs  <- v0  * (WT/70)^1.0
p   <- c(KA = ka0, CLs = CLs, Vs = Vs, KOUT = kout, BASE = base0, IC50 = ic50)
```

---

## 7. `eventTable` / `units` package errors → use unit-free `et()` pipe form

**Symptom:** Building an event table with units fails because the `units` package isn't
installed.

**Fix:** use the unit-free `et()` pipe form for regimens:
```r
ev <- et(amt = dose_mg, ii = 24, until = (days-1)*24, cmt = "depot") |>
      et(seq(0, days*24, by = 1))
```

---

## 8. `can only specify either 'cmt', 'ytype', 'state' or 'var'`

**Symptom:** `nlmixr2` aborts at dataset assembly with
*"can only specify either 'cmt', 'ytype', 'state' or 'var'"*. Common with NONMEM-format inputs
(and the `nlmixr2data` `theo_sd`/`Theoph` examples, which ship a native `CMT` column).

**Cause:** The input already contains a compartment specifier column (`CMT`, `ytype`, `state`, or
`var`, in any case) AND the pipeline adds its own endpoint-name `cmt` (gotcha #3). nlmixr2 sees
two specifiers and refuses.

**Fix:** drop any pre-existing specifier column **before** assigning the endpoint-name `cmt`:
```r
drop_spec <- names(d)[tolower(names(d)) %in% c("cmt","ytype","state","var")]
if (length(drop_spec)) d <- d[, setdiff(names(d), drop_spec), drop = FALSE]
d$cmt <- ifelse(d$EVID == 1, "depot", "cp")   # our endpoint-name mapping wins
```
(Both `03_fit_pk.R` and `04_fit_pd.R` do this automatically.)

---

## 9. `object 'DVID' not found` inside a `dplyr::filter()` guard

**Symptom:** A filter meant to keep only the PK (or PD) endpoint errors with
*"object 'DVID' not found"* on PK-only data, even though the expression contains a
`"DVID" %in% names(d)` guard.

**Cause:** dplyr's non-standard evaluation still resolves the `DVID == <label>` **symbol** at
parse time, before the `%in%` guard can short-circuit — so a missing `DVID` column throws.

**Fix:** compute the keep-mask in **base R** (regular `&&`/`||` short-circuit) *before* filtering,
then subset:
```r
keep_pk_obs <- if ("DVID" %in% names(d)) (d$EVID==0 & as.character(d$DVID)==pk_lab) else (d$EVID==0)
pk_dat <- d[d$EVID==1 | keep_pk_obs, , drop = FALSE]
```

---

## 10. General reminders

- **Every** R invocation: `.libPaths(c("/workspace/.Rlib", .libPaths()))` and
  `export R_LIBS_USER=/workspace/.Rlib` — the stack lives in `/workspace/.Rlib`, not the base lib.
- rxode2 compiles models with **gcc/cc + gfortran**; if compilation fails, verify both are on PATH.
- Save fits with `saveRDS()` — refitting is the slow part; reload with `readRDS()` to iterate on
  plots/simulation without re-estimating.

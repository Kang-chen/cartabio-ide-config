# Model Library — PK & PD templates (nlmixr2)

Reference model code you adapt per dataset. All models assume the R preamble
`.libPaths(c("/workspace/.Rlib", .libPaths()))` and `library(nlmixr2)`.

**The most important idea:** the PD structure is **chosen from the data's delay signature**
(Stage 2), not hardcoded. Diagnose the lag between concentration Tmax and the response
peak/nadir, and check the concentration–effect loop for hysteresis:

| Delay signature | Interpretation | PD structure |
|-----------------|----------------|--------------|
| Effect peaks ≈ with concentration; no loop | no distribution/turnover delay | **Direct Emax/Imax** |
| Effect lags concn by minutes–hours; counter-clockwise hysteresis loop | distributional delay | **Effect-compartment (link)** |
| Effect lags concn by many hours–days (e.g. ~42 h) | turnover-mediated | **Indirect-response (turnover)** |

---

## Population PK — 1-compartment oral, allometric (baseline template)

```r
pk_model <- function() {
  ini({
    tka <- log(0.5)      # log absorption rate (1/h)   -- adjust to data
    tcl <- log(0.13)     # log clearance (L/h)
    tv  <- log(8)        # log central volume (L)
    eta.ka ~ 0.3         # BSV (log-normal)
    eta.cl ~ 0.1
    eta.v  ~ 0.1
    add.err  <- 0.5      # additive residual
    prop.err <- 0.1      # proportional residual
  })
  model({
    ka <- exp(tka + eta.ka)
    cl <- exp(tcl + eta.cl) * (WT/70)^0.75    # allometric (drop *(WT/70)^.. if no weight)
    v  <- exp(tv  + eta.v ) * (WT/70)^1.0
    d/dt(depot)  <- -ka*depot
    d/dt(center) <-  ka*depot - (cl/v)*center
    cp <- center/v
    cp ~ add(add.err) + prop(prop.err)        # endpoint name 'cp' (see troubleshooting #3)
  })
}
fit_pk <- nlmixr2(pk_model, dat, est = "focei",
                  control = foceiControl(print = 0, outerOpt = "bobyqa"))
```
- **2-compartment:** add `d/dt(peripheral)`, inter-compartmental `q`, `v2`; compare by OBJF/AIC.
- Drop covariate terms if the covariate is absent; only keep effects the data support (check %RSE).
- Verified warfarin typical values (sanity anchor): CL ≈ **0.136 L/h/70kg**, V ≈ **7.9 L**,
  ka ≈ **0.55/h**.

---

## PD option A — Direct Emax / Imax (no delay)

Effect is an instantaneous function of the (PK-fixed) concentration `cp`.
```r
model({
  # PK params fixed from the PK fit (inject numeric values)
  ...PK block with fixed thetas/etas producing cp...
  emax <- exp(temax)
  ec50 <- exp(tec50 + eta.ec50)
  e0   <- exp(te0)
  # stimulation:  eff <- e0 * (1 + emax*cp/(ec50+cp))
  # inhibition:   eff <- e0 * (1 - imax*cp/(ic50+cp))   # imax<-1 if unidentifiable
  eff <- e0 * (1 - 1*cp/(ec50+cp))
  eff ~ add(pd.add) + prop(pd.prop)
})
```

## PD option B — Effect-compartment (link) model (moderate hysteresis)

A hypothetical effect compartment with rate `ke0` drives the effect; collapses the hysteresis
loop.
```r
model({
  ...PK block producing cp...
  ke0  <- exp(tke0)
  ec50 <- exp(tec50 + eta.ec50)
  emax <- exp(temax); e0 <- exp(te0)
  d/dt(ce) <- ke0*(cp - ce)          # effect-site concentration
  eff <- e0 * (1 - emax*ce/(ec50+ce))
  eff ~ add(pd.add) + prop(pd.prop)
})
```
- `ke0` sets the delay (effect-site t½ = ln2/ke0). Initialize from the observed lag.

## PD option C — Indirect-response / turnover (large delay) — warfarin branch

Drug inhibits synthesis (`kin`) or stimulates loss (`kout`) of the response variable. Baseline =
`kin/kout`. This is the structure the warfarin ~42 h delay required.
```r
model({
  ...PK block producing cp...
  imax <- 1                          # fix at 1 if unidentifiable (troubleshooting #5)
  kout <- exp(tkout)
  base <- exp(tbase + eta.base)      # baseline response
  ic50 <- exp(tic50 + eta.ic50)
  kin  <- kout * base
  pca(0)    <- base
  d/dt(pca) <- kin*(1 - imax*cp/(ic50 + cp)) - kout*pca   # inhibition of synthesis
  # stimulation-of-loss variant: d/dt(R) <- kin - kout*(1 + emax*cp/(ec50+cp))*R
  pca ~ add(pd.add) + prop(pd.prop)
})
```
Verified warfarin typical values (sanity anchor): baseline PCA ≈ **98%**, kout ≈ **0.052/h**
(response t½ ≈ 13 h), IC50 ≈ **1.07 mg/L**, `Imax = 1 (fixed)`, condition # ≈ 40.

---

## Sequential PD fitting (PK fixed)

Fix the PK thetas/etas/residual at the PK-model estimates, then estimate only PD parameters.
Practical approach used for warfarin: write the combined model, then **string-substitute** the
fixed PK numeric values into placeholders before `nlmixr2()`:
```r
# extract with as.numeric() (troubleshooting #2), inject into model source, eval(parse(...))
TKA <- as.numeric(fit_pk$parFixedDf["tka","Estimate"]);  # etc.
```
Report that the fit is **sequential** (PK uncertainty not propagated) as a limitation and offer a
**simultaneous** fit as a next step.

---

## Initialization heuristics

- `tcl` init ≈ dose / (AUC observed); `tv` init ≈ dose / Cmax; `tka` init from absorption phase.
- `tbase`/`e0` init ≈ mean baseline response; `kout` init from the response recovery half-life
  (`kout ≈ ln2 / t½_recovery`); `ic50`/`ec50` init near the median observed concentration.
- BSV inits ~0.1 (≈32% CV); loosen if shrinkage is high, tighten if the covariance step fails.

---

## Evaluation (default tier)

- GOF: OBS vs PRED and vs IPRED (line of identity); CWRES vs TIME and vs PRED (centered on 0).
- Parameter precision: `fit$parFixedDf` gives Estimate, %RSE, back-transformed 95% CI; report BSV
  %CV and shrinkage; report the **condition number**.
- **VPC** (simulation-only): use nlmixr2's VPC (`vpcPlot()` / the `vpc`/`tidyvpc` packages,
  installed with the stack) — overlay observed percentiles on simulated prediction intervals.
- **Bootstrap** (opt-in only): resample subjects and refit N times for empirical CIs — expensive;
  document runtime; consider a larger machine.

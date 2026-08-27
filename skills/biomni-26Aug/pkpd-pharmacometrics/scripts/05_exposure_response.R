#!/usr/bin/env Rscript
# Stage 5 — exposure-response simulation + TARGET-GATED dose recommendation. SKIPPED if PK_ONLY.
# HARD GUARDRAIL: refuses to emit a dose without an explicit therapeutic target window
# (user-supplied OR literature-cited). NEVER invents a target. Every output is disclaimed.
# rxode2 gotchas applied: 'conc' not 'cp', as.numeric() strips names, bake WT into params.
.libPaths(c("/workspace/.Rlib", .libPaths()))
suppressPackageStartupMessages({library(rxode2); library(dplyr); library(ggplot2)})

ing <- readRDS("/workspace/pkpd_ingest.rds")
if (ing$PK_ONLY) { cat("PK_ONLY: no PD/target -> Stage 5 skipped (no dose recommendation).\n"); quit(save="no") }
fit_pk <- readRDS("/workspace/fit_pk.rds"); fit_pd <- readRDS("/workspace/fit_pd.rds")
FIG <- Sys.getenv("PKPD_FIGDIR","/mnt/results/figures"); TAB <- Sys.getenv("PKPD_OUTDIR","/mnt/results/tables")
theme_set(theme_bw(base_size=12)+theme(text=element_text(family="Liberation Sans")))

# ================= HARD GUARDRAIL: target window required =================
# Supply via env as "lo,hi" on the modeled endpoint scale, plus a REQUIRED source string.
TARGET   <- Sys.getenv("PKPD_TARGET", "")          # e.g. "20,30"
TARGET_SRC <- Sys.getenv("PKPD_TARGET_SOURCE", "") # e.g. "O'Leary & Abbrecht 1981 (PCA 20-30%)"
if (!nzchar(TARGET) || !nzchar(TARGET_SRC)) {
  cat("\n*** DOSE GUARDRAIL TRIGGERED ***\n")
  cat("No therapeutic target window + source supplied. This skill does NOT invent targets.\n")
  cat("Action required: obtain an explicit target window either from the user OR via the\n")
  cat("LiteratureSearch tool (record the citation), then set PKPD_TARGET='lo,hi' and\n")
  cat("PKPD_TARGET_SOURCE='<citation>'. No dose will be emitted until then.\n")
  quit(save="no", status=2)
}
tw <- as.numeric(strsplit(TARGET,",")[[1]]); tlo<-min(tw); thi<-max(tw); tmid<-mean(tw)
cat(sprintf("Target window: %.3g-%.3g (source: %s)\n", tlo, thi, TARGET_SRC))

# ---- extract params (as.numeric strips names -> troubleshooting #2) ----
Ppk<-fit_pk$parFixedDf; Ppd<-fit_pd$parFixedDf
ka0<-as.numeric(exp(Ppk["tka","Estimate"])); cl0<-as.numeric(exp(Ppk["tcl","Estimate"])); v0<-as.numeric(exp(Ppk["tv","Estimate"]))
struct <- if ("PD_structure" %in% colnames(read.csv(file.path(TAB,"pd_parameters.csv")))) 
  as.character(read.csv(file.path(TAB,"pd_parameters.csv"))$PD_structure[1]) else "indirect"

# simulation model — 'conc' NOT 'cp' (troubleshooting #1); indirect-response shown (adapt per struct)
if (struct=="indirect") {
  kout<-as.numeric(exp(Ppd["tkout","Estimate"])); base0<-as.numeric(exp(Ppd["tbase","Estimate"])); ic50<-as.numeric(exp(Ppd["tic50","Estimate"]))
  sim_mod <- rxode2({
    kin <- KOUT*BASE
    d/dt(depot)  <- -KA*depot
    d/dt(center) <-  KA*depot - (CLs/Vs)*center
    conc <- center/Vs
    pca(0) <- BASE
    d/dt(pca) <- kin*(1 - 1*conc/(IC50+conc)) - KOUT*pca
  })
  effect_col <- "pca"
} else if (struct=="link") {
  # FIX 4: effect-compartment (link) model. Previously 'link' silently fell through to direct Emax
  # on 'conc', dropping ke0 entirely. Here ke0 drives an effect-site concentration 'ce' that lags
  # 'conc', and Emax is driven off 'ce' (E0, EC50, ke0 all taken from the PD fit). 'conc' not 'cp'.
  e0<-as.numeric(exp(Ppd["te0","Estimate"])); ec50<-as.numeric(exp(Ppd["tec50","Estimate"])); ke0<-as.numeric(exp(Ppd["tke0","Estimate"]))
  sim_mod <- rxode2({
    d/dt(depot)  <- -KA*depot
    d/dt(center) <-  KA*depot - (CLs/Vs)*center
    conc <- center/Vs
    d/dt(ce) <- KE0*(conc - ce)
    eff  <- E0*(1 - ce/(EC50+ce))
  })
  effect_col <- "eff"
} else {
  # direct Emax: effect = e0*(1 - conc/(ec50+conc))
  e0<-as.numeric(exp(Ppd["te0","Estimate"])); ec50<-as.numeric(exp(Ppd["tec50","Estimate"]))
  sim_mod <- rxode2({
    d/dt(depot)  <- -KA*depot
    d/dt(center) <-  KA*depot - (CLs/Vs)*center
    conc <- center/Vs
    eff  <- E0*(1 - conc/(EC50+conc))
  })
  effect_col <- "eff"
}

simulate_regimen <- function(dose_mg, WT=70, days=30, ii=24) {
  CLs <- cl0*(WT/70)^0.75; Vs <- v0*(WT/70)^1.0        # bake WT in (troubleshooting #6)
  ev <- et(amt=dose_mg, ii=ii, until=(days-1)*ii, cmt="depot") |> et(seq(0, days*ii, by=1))
  p <- if (struct=="indirect") c(KA=ka0,CLs=CLs,Vs=Vs,KOUT=kout,BASE=base0,IC50=ic50)
       else if (struct=="link") c(KA=ka0,CLs=CLs,Vs=Vs,E0=e0,EC50=ec50,KE0=ke0)
       else c(KA=ka0,CLs=CLs,Vs=Vs,E0=e0,EC50=ec50)
  as.data.frame(rxSolve(sim_mod, p, ev))
}
ss_window <- function(x, ii=24) x[x$time >= (max(x$time)-ii), ]

# ---- dose grid -> steady-state effect ----
grid <- seq(as.numeric(Sys.getenv("PKPD_DOSE_MIN","0.5")),
            as.numeric(Sys.getenv("PKPD_DOSE_MAX","15")),
            by=as.numeric(Sys.getenv("PKPD_DOSE_STEP","0.25")))
res <- do.call(rbind, lapply(grid, function(dm){
  w<-ss_window(simulate_regimen(dm)); data.frame(dose=dm, eff_mean=mean(w[[effect_col]]),
    eff_min=min(w[[effect_col]]), eff_max=max(w[[effect_col]]), conc_mean=mean(w$conc)) }))
write.csv(res, file.path(TAB,"dose_response_steadystate.csv"), row.names=FALSE)

# --- FIX 1: robust, monotonic-safe effect->dose inversion --------------------------------------
# The steady-state exposure-response SATURATES, so eff_mean vs dose has tied / non-monotonic
# values (ambiguous "dual" inverse) and off-range targets returned a SILENT NA with plain approx().
# This helper: (a) drops non-finite points and orders by dose; (b) clamps an unreachable target to
# the achievable effect range with an EXPLICIT warning; (c) restricts to the strictly monotonic
# envelope (cummax/cummin) and de-duplicates tied effects (smallest dose achieving each level);
# (d) interpolates with rule=2 so it NEVER emits a silent NA dose.
invert_effect_to_dose <- function(dose, eff, target, label="") {
  ok <- is.finite(dose) & is.finite(eff); dose <- dose[ok]; eff <- eff[ok]
  if (length(dose) < 2L) stop("invert_effect_to_dose: need >=2 finite (dose,eff) points")
  if (is.na(target)) stop("invert_effect_to_dose: target is NA")
  o <- order(dose); dose <- dose[o]; eff <- eff[o]
  elo <- min(eff); ehi <- max(eff); tgt <- target
  if (tgt < elo) { warning(sprintf("dose_for%s: target %.4g below achievable effect range [%.4g, %.4g]; clamping to %.4g (dose is an extrapolation bound).", label, tgt, elo, ehi, elo)); tgt <- elo }
  if (tgt > ehi) { warning(sprintf("dose_for%s: target %.4g above achievable effect range [%.4g, %.4g]; clamping to %.4g (dose is an extrapolation bound).", label, tgt, elo, ehi, ehi)); tgt <- ehi }
  dir  <- if (eff[length(eff)] >= eff[1]) 1 else -1     # overall dose->effect direction
  mono <- if (dir > 0) cummax(eff) else cummin(eff)     # strictly monotone envelope vs increasing dose
  keep <- !duplicated(mono)                             # dedupe ties -> smallest dose per effect level
  dd <- dose[keep]; ee <- mono[keep]
  if (dir < 0) { ee <- rev(ee); dd <- rev(dd) }         # approx() needs increasing x
  as.numeric(approx(x=ee, y=dd, xout=tgt, rule=2, ties="ordered")$y)   # rule=2 -> clamped, never NA
}
dose_for <- function(target) invert_effect_to_dose(res$dose, res$eff_mean, target)
d_mid<-dose_for(tmid); d_lo<-dose_for(thi); d_hi<-dose_for(tlo)   # note: lower effect target -> higher dose for inhibition
cat(sprintf("\nRecommended dose (70 kg): %.1f mg/day (range %.1f-%.1f)\n", d_mid, min(d_lo,d_hi,na.rm=TRUE), max(d_lo,d_hi,na.rm=TRUE)))
cat("*** Model-based extrapolation for methodological illustration; NOT clinical dosing guidance. ***\n")
if (isTRUE(ing$flags$single_level)) cat("*** Single observed dose level -> alternative doses are extrapolation. ***\n")

# dose by weight (if allometric)
wt_tab <- do.call(rbind, lapply(c(50,70,90), function(w){
  rr<-do.call(rbind,lapply(grid,function(dm){x<-ss_window(simulate_regimen(dm,WT=w));data.frame(dose=dm,eff=mean(x[[effect_col]]))}))
  data.frame(WT=w, dose_mid=invert_effect_to_dose(rr$dose,rr$eff,tmid,sprintf("[WT=%g]",w)),
             dose_lo=invert_effect_to_dose(rr$dose,rr$eff,thi,sprintf("[WT=%g]",w)),
             dose_hi=invert_effect_to_dose(rr$dose,rr$eff,tlo,sprintf("[WT=%g]",w))) })) %>%
  mutate(dose_per_kg=round(dose_mid/WT,3))
write.csv(wt_tab, file.path(TAB,"dose_by_weight.csv"), row.names=FALSE)

# ---- figures ----
p1 <- ggplot(res,aes(dose,eff_mean))+geom_ribbon(aes(ymin=eff_min,ymax=eff_max),alpha=.15,fill="#0279EE")+
  geom_line(color="#0279EE",linewidth=1)+annotate("rect",xmin=-Inf,xmax=Inf,ymin=tlo,ymax=thi,alpha=.15,fill="#75A025")+
  geom_vline(xintercept=d_mid,color="#D4A04A",linewidth=1)+labs(x="Once-daily dose (mg)",y="Steady-state effect",
  title="Exposure-response",subtitle=paste0("Target ",tlo,"-",thi," (",TARGET_SRC,")"))
ggsave(file.path(FIG,"fig_exposure_response.png"),p1,width=7,height=5,dpi=150)
ggsave(file.path(FIG,"fig_exposure_response.svg"),p1,width=7,height=5)

prof <- simulate_regimen(d_mid)
p2 <- ggplot(prof,aes(time/24,.data[[effect_col]]))+geom_line(color="#75A025",linewidth=1)+
  annotate("rect",xmin=-Inf,xmax=Inf,ymin=tlo,ymax=thi,alpha=.15,fill="#75A025")+
  labs(x="Time (days)",y="Effect",title="Steady-state approach at recommended dose")
ggsave(file.path(FIG,"fig_ss_profile.png"),p2,width=7,height=4.5,dpi=150)

saveRDS(list(res=res, wt=wt_tab, dose_mid=d_mid, target=c(tlo,thi), source=TARGET_SRC), "/workspace/sim_results.rds")
cat("\nStage 5 done. Dose tables + figures written (target-gated).\n")
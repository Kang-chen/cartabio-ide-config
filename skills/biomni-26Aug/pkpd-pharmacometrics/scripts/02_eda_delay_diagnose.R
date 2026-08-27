#!/usr/bin/env Rscript
# Stage 2 — EDA + delay diagnosis. Plots raw concentration (and response) vs time and computes
# the concentration->effect DELAY SIGNATURE that drives PD-structure selection (Stage 4).
# Does NOT hardcode a PD model — it recommends one and prints the justification.
.libPaths(c("/workspace/.Rlib", .libPaths()))
suppressPackageStartupMessages({library(dplyr); library(ggplot2)})

ing   <- readRDS("/workspace/pkpd_ingest.rds"); d <- ing$data; PK_ONLY <- ing$PK_ONLY
FIG   <- Sys.getenv("PKPD_FIGDIR", "/mnt/results/figures"); dir.create(FIG, TRUE, TRUE)
theme_set(theme_bw(base_size = 12) + theme(text = element_text(family = "Liberation Sans")))

# Identify PK vs PD endpoint labels (edit if your DVID labels differ)
pk_lab <- Sys.getenv("PKPD_PKLAB", "cp")
pd_lab <- Sys.getenv("PKPD_PDLAB", "pca")
has_dvid <- "DVID" %in% names(d)
obs <- d %>% filter(EVID == 0)

# ---- raw spaghetti plots ----
if (has_dvid) {
  p <- ggplot(obs, aes(TIME, DV, group = ID)) + geom_line(alpha=.3) +
       stat_summary(aes(group=1), fun=mean, geom="line", color="#D4A04A", linewidth=1.2) +
       facet_wrap(~DVID, scales="free_y") + labs(x="Time (h)", y="Observed", title="Raw data by endpoint")
} else {
  p <- ggplot(obs, aes(TIME, DV, group = ID)) + geom_line(alpha=.3) +
       stat_summary(aes(group=1), fun=mean, geom="line", color="#D4A04A", linewidth=1.2) +
       labs(x="Time (h)", y="Concentration", title="Raw PK data")
}
ggsave(file.path(FIG,"fig_raw_data.png"), p, width=8, height=4.5, dpi=150)
ggsave(file.path(FIG,"fig_raw_data.svg"), p, width=8, height=4.5)

# ---- delay signature (only if a PD endpoint exists) ----
signature <- list(PK_ONLY = PK_ONLY)
if (!PK_ONLY && has_dvid) {
  mean_pk <- obs %>% filter(DVID==pk_lab) %>% group_by(TIME) %>% summarise(m=mean(DV,na.rm=TRUE))
  mean_pd <- obs %>% filter(DVID==pd_lab) %>% group_by(TIME) %>% summarise(m=mean(DV,na.rm=TRUE))
  tmax_pk <- mean_pk$TIME[which.max(mean_pk$m)]
  # response extremum: use whichever deviates more from baseline (nadir for inhibition)
  base_pd <- mean_pd$m[which.min(mean_pd$TIME)]
  t_nadir <- mean_pd$TIME[which.min(mean_pd$m)]
  t_peak  <- mean_pd$TIME[which.max(mean_pd$m)]
  t_effect<- if (abs(min(mean_pd$m)-base_pd) >= abs(max(mean_pd$m)-base_pd)) t_nadir else t_peak
  delay_h <- t_effect - tmax_pk
  signature <- c(signature, list(tmax_pk=tmax_pk, t_effect=t_effect, delay_h=delay_h,
                                 baseline=base_pd, direction=if (t_effect==t_nadir) "decrease" else "increase"))
  # recommendation
  rec <- if (delay_h < 1) "Direct Emax/Imax" else if (delay_h < 8) "Effect-compartment (link)" else
         "Indirect-response (turnover)"
  signature$recommended_pd <- rec
  cat("\n============ DELAY SIGNATURE ============\n")
  cat(sprintf("  Concentration Tmax : %.1f h\n", tmax_pk))
  cat(sprintf("  Response extremum  : %.1f h (%s from baseline %.1f)\n", t_effect, signature$direction, base_pd))
  cat(sprintf("  Delay (effect-conc): %.1f h\n", delay_h))
  cat(sprintf("  >> Recommended PD structure: %s\n", rec))
  cat("     (<1h -> direct Emax; 1-8h -> effect-compartment; >8h -> indirect-response)\n")
  cat("     Surface this to the user with the measured delay; let them override.\n")
  cat("=========================================\n")

  # hysteresis loop plot (conc vs effect, time-ordered) — visual confirmation
  merged <- inner_join(mean_pk, mean_pd, by="TIME", suffix=c("_pk","_pd")) %>% arrange(TIME)
  ph <- ggplot(merged, aes(m_pk, m_pd)) + geom_path(color="#0279EE", arrow=grid::arrow(length=unit(.15,"cm"))) +
        geom_point(aes(color=TIME)) + scale_color_viridis_c() +
        labs(x="Mean concentration", y="Mean response", title="Concentration-effect (hysteresis)")
  ggsave(file.path(FIG,"fig_hysteresis.png"), ph, width=6, height=4.5, dpi=150)
} else {
  cat("\nPK_ONLY: skipping delay diagnosis (no PD endpoint). PD & dose stages will be skipped.\n")
}
saveRDS(signature, "/workspace/pkpd_signature.rds")
cat("\nWrote raw-data figure(s); delay signature saved.\n")

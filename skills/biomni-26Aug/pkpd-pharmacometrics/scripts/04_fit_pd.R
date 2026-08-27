#!/usr/bin/env Rscript
# Stage 4 — sequential population PD fit. SKIPPED automatically if PK_ONLY.
# PD STRUCTURE IS CHOSEN FROM THE DELAY SIGNATURE (Stage 2): direct Emax / effect-compartment /
# indirect-response. Do NOT hardcode. PK params are FIXED (sequential). See model_library.md.
.libPaths(c("/workspace/.Rlib", .libPaths()))
suppressPackageStartupMessages({library(nlmixr2); library(dplyr); library(ggplot2)})

ing <- readRDS("/workspace/pkpd_ingest.rds"); d <- ing$data
if (ing$PK_ONLY) { cat("PK_ONLY: no PD endpoint -> Stage 4 skipped.\n"); quit(save="no", status=0) }
sig <- readRDS("/workspace/pkpd_signature.rds")
fit_pk <- readRDS("/workspace/fit_pk.rds")
FIG <- Sys.getenv("PKPD_FIGDIR","/mnt/results/figures"); TAB <- Sys.getenv("PKPD_OUTDIR","/mnt/results/tables")
theme_set(theme_bw(base_size=12)+theme(text=element_text(family="Liberation Sans")))
pk_lab <- Sys.getenv("PKPD_PKLAB","cp"); pd_lab <- Sys.getenv("PKPD_PDLAB","pca")

# PD structure: honor user override (PKPD_PDSTRUCT) else the delay-signature recommendation
STRUCT <- Sys.getenv("PKPD_PDSTRUCT", "")
if (!nzchar(STRUCT)) STRUCT <- switch(as.character(sig$recommended_pd),
  "Direct Emax/Imax"="emax", "Effect-compartment (link)"="link", "Indirect-response (turnover)"="indirect", "indirect")
cat("PD structure chosen:", STRUCT, "(delay =", round(sig$delay_h,1), "h)\n")

# Fixed PK numeric values (as.numeric strips names -> troubleshooting #2)
P <- fit_pk$parFixedDf
TKA<-as.numeric(P["tka","Estimate"]); TCL<-as.numeric(P["tcl","Estimate"]); TV<-as.numeric(P["tv","Estimate"])
# NOTE: for a fully rigorous sequential fit, also fix individual etas; here we fix population PK
# and estimate PD (+PD BSV). This is the pragmatic sequential approach — state it as a limitation.

# nlmixr2 dataset with BOTH endpoints; obs cmt = endpoint name.
# Drop native compartment specifiers first (see 03_fit_pk.R note) to avoid the
# "can only specify either 'cmt'/'ytype'/'state'/'var'" nlmixr2 error.
drop_spec <- names(d)[tolower(names(d)) %in% c("cmt","ytype","state","var")]
d2 <- if (length(drop_spec)) d[, setdiff(names(d), drop_spec), drop=FALSE] else d
pd_dat <- d2 %>% mutate(cmt = ifelse(EVID==1,"depot", ifelse(DVID==pk_lab,"cp","pca"))) %>%
  rename(dv=DV) %>% mutate(dv=ifelse(EVID==1,NA,dv))
if (!("WT" %in% names(pd_dat)) && "WT" %in% names(d)) pd_dat$WT <- d$WT[match(pd_dat$ID,d$ID)]
ALLO <- "WT" %in% names(pd_dat) && !all(is.na(pd_dat$WT))

# ---- build model source per structure ----
pk_block <- sprintf("
    ka <- exp(%f); cl <- exp(%f)%s; v <- exp(%f)%s
    d/dt(depot) <- -ka*depot
    d/dt(center) <- ka*depot - (cl/v)*center
    cp <- center/v
    cp ~ add(add.err.pk)+prop(prop.err.pk)",
  TKA, TCL, if(ALLO)"*(WT/70)^0.75" else "", TV, if(ALLO)"*(WT/70)^1.0" else "")

pd_block <- switch(STRUCT,
  emax = "
    e0 <- exp(te0); ec50 <- exp(tec50+eta.ec50); imax <- 1
    eff <- e0*(1 - imax*cp/(ec50+cp))
    eff ~ add(pd.add)+prop(pd.prop)",
  link = "
    e0 <- exp(te0); ec50 <- exp(tec50+eta.ec50); ke0 <- exp(tke0); imax <- 1
    d/dt(ce) <- ke0*(cp - ce)
    eff <- e0*(1 - imax*ce/(ec50+ce))
    eff ~ add(pd.add)+prop(pd.prop)",
  indirect = "
    kout <- exp(tkout); base <- exp(tbase+eta.base); ic50 <- exp(tic50+eta.ic50); imax <- 1
    kin <- kout*base
    pca(0) <- base
    d/dt(pca) <- kin*(1 - imax*cp/(ic50+cp)) - kout*pca
    pca ~ add(pd.add)+prop(pd.prop)")

# endpoint name must match residual LHS: emax/link -> 'eff'; indirect -> 'pca'
pd_endpoint <- if (STRUCT=="indirect") "pca" else "eff"
if (pd_endpoint=="eff") {
  eff_rows <- pd_dat$cmt=="pca"
  pd_dat$cmt[eff_rows] <- "eff"
  # FIX: align DVID with the renamed cmt on the SAME rows so cmt and dvid agree
  # (nlmixr2 rejects rows where cmt != dvid: "'cmt' and 'dvid' specify different compartments")
  if ("DVID" %in% names(pd_dat)) { pd_dat$DVID <- as.character(pd_dat$DVID); pd_dat$DVID[eff_rows] <- "eff" }
}

ini_block <- switch(STRUCT,
  emax = "te0<-log(max(d$DV[d$EVID==0 & d$DVID==pd_lab],na.rm=TRUE)); tec50<-log(1); eta.ec50~0.1; pd.add<-1; pd.prop<-0.1",
  link = "te0<-log(max(d$DV[d$EVID==0 & d$DVID==pd_lab],na.rm=TRUE)); tec50<-log(1); tke0<-log(0.1); eta.ec50~0.1; pd.add<-1; pd.prop<-0.1",
  indirect = "tkout<-log(0.05); tbase<-log(mean(d$DV[d$EVID==0 & d$DVID==pd_lab],na.rm=TRUE)); tic50<-log(1); eta.base~0.05; eta.ic50~0.1; pd.add<-1; pd.prop<-0.1")

mod_src <- sprintf("function(){ ini({ add.err.pk<-%f; prop.err.pk<-%f; %s }); model({ %s
    %s }) }",
  as.numeric(if("add.err"%in%rownames(P)) P["add.err","Estimate"] else 1),
  as.numeric(if("prop.err"%in%rownames(P)) P["prop.err","Estimate"] else 0.1),
  ini_block, pk_block, pd_block)
pd_model <- eval(parse(text=mod_src))

fit <- tryCatch(nlmixr2(pd_model, pd_dat, est="focei", control=foceiControl(print=0, outerOpt="bobyqa")),
                error=function(e){cat("PD fit error:",conditionMessage(e),"\n"); e})
if (inherits(fit,"error")) quit(save="no", status=1)
saveRDS(fit,"/workspace/fit_pd.rds")
cat("\nPD (",STRUCT,") OBJF:",round(fit$objf,2)," AIC:",round(AIC(fit),2),
    " cond#:",tryCatch(round(fit$conditionNumber,1),error=function(e)NA),"\n")
print(fit$parFixedDf)
write.csv(cbind(Parameter=rownames(fit$parFixedDf), fit$parFixedDf, PD_structure=STRUCT),
          file.path(TAB,"pd_parameters.csv"), row.names=FALSE)

df<-as.data.frame(fit); library(patchwork)
gof<-function(x,xl) ggplot(df,aes(.data[[x]],DV))+geom_point(alpha=.5,color="#75A025")+
  geom_abline(slope=1,intercept=0,color="#D4A04A")+labs(x=xl,y="Observed response")
g<-(gof("PRED","Population predicted")|gof("IPRED","Individual predicted"))
ggsave(file.path(FIG,"fig_pd_gof.png"),g,width=9,height=4,dpi=150)
ggsave(file.path(FIG,"fig_pd_gof.svg"),g,width=9,height=4)
tryCatch({v<-vpcPlot(fit,n=200); ggsave(file.path(FIG,"fig_pd_vpc.png"),v,width=7,height=5,dpi=150)},
         error=function(e) cat("PD VPC skipped:",conditionMessage(e),"\n"))
cat("\nPD stage done (structure =",STRUCT,").\n")
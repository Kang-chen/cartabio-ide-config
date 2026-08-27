#!/usr/bin/env Rscript
# Stage 3 — population PK fit (1-compartment oral, optional allometric weight scaling).
# FOCEi with bobyqa fallback on 'false convergence'. Produces parameter table, GOF, VPC.
# See references/model_library.md for 2-compartment and covariate variants.
.libPaths(c("/workspace/.Rlib", .libPaths()))
suppressPackageStartupMessages({library(nlmixr2); library(dplyr); library(ggplot2)})

ing  <- readRDS("/workspace/pkpd_ingest.rds"); d <- ing$data
FIG  <- Sys.getenv("PKPD_FIGDIR","/mnt/results/figures"); TAB <- Sys.getenv("PKPD_OUTDIR","/mnt/results/tables")
dir.create(FIG,TRUE,TRUE); dir.create(TAB,TRUE,TRUE)
theme_set(theme_bw(base_size=12)+theme(text=element_text(family="Liberation Sans")))
ALLO <- "WT" %in% names(d) && !all(is.na(d$WT))     # allometric only if weight present

# nlmixr2 dataset: dose rows cmt="depot", PK obs cmt="cp" (ENDPOINT name, see troubleshooting #3)
pk_lab <- Sys.getenv("PKPD_PKLAB","cp")
# Keep dose rows + PK observations. If a DVID column exists, restrict obs to the PK endpoint;
# if not (PK-only data), all EVID==0 rows are PK observations. (Guard evaluated BEFORE filter to
# avoid dplyr NSE trying to resolve a non-existent DVID symbol.)
keep_pk_obs <- if ("DVID" %in% names(d)) (d$EVID==0 & as.character(d$DVID)==pk_lab) else (d$EVID==0)
pk_dat <- d[d$EVID==1 | keep_pk_obs, , drop=FALSE]
# Drop any native compartment specifiers (CMT/ytype/state/var, any case). nlmixr2 errors if BOTH a
# native CMT and our endpoint-name cmt are present ("can only specify either 'cmt'/'ytype'/...").
# We define the mapping ourselves via endpoint names (troubleshooting #3).
drop_spec <- names(pk_dat)[tolower(names(pk_dat)) %in% c("cmt","ytype","state","var")]
if (length(drop_spec)) pk_dat <- pk_dat[, setdiff(names(pk_dat), drop_spec), drop=FALSE]
pk_dat <- pk_dat %>%
  mutate(cmt = ifelse(EVID==1, "depot", "cp")) %>%
  rename(dv=DV) %>% mutate(dv = ifelse(EVID==1, NA, dv))
if (ALLO && !("WT" %in% names(pk_dat))) pk_dat$WT <- d$WT[match(pk_dat$ID, d$ID)]

pk_model <- function() {
  ini({ tka<-log(0.5); tcl<-log(0.13); tv<-log(8)
        eta.ka~0.3; eta.cl~0.1; eta.v~0.1
        add.err<-0.5; prop.err<-0.1 })
  model({
    ka <- exp(tka+eta.ka)
    cl <- exp(tcl+eta.cl) * (WT/70)^0.75      # allometric; if !ALLO the (WT/70) term is edited out
    v  <- exp(tv +eta.v ) * (WT/70)^1.0
    d/dt(depot)  <- -ka*depot
    d/dt(center) <-  ka*depot - (cl/v)*center
    cp <- center/v
    cp ~ add(add.err)+prop(prop.err)
  })
}
if (!ALLO) {  # strip allometric terms cleanly when no weight covariate
  src <- deparse(body(pk_model)); src <- gsub("\\* \\(WT/70\\)\\^0.75","",src); src <- gsub("\\* \\(WT/70\\)\\^1.0","",src)
  body(pk_model) <- eval(parse(text=paste(src, collapse="\n")))
}

fit <- tryCatch(nlmixr2(pk_model, pk_dat, est="focei", control=foceiControl(print=0)),
                error=function(e) e)
# bobyqa fallback on convergence trouble (troubleshooting #4)
if (inherits(fit,"error") || (!is.null(fit$message) && grepl("false convergence", paste(fit$message),ignore.case=TRUE))) {
  cat("Retrying PK fit with outerOpt='bobyqa' (false convergence / error).\n")
  fit <- nlmixr2(pk_model, pk_dat, est="focei", control=foceiControl(print=0, outerOpt="bobyqa"))
}
saveRDS(fit, "/workspace/fit_pk.rds")
cat("\nPK fit OBJF:", round(fit$objf,2), " AIC:", round(AIC(fit),2),
    " cond#:", tryCatch(round(fit$conditionNumber,1),error=function(e) NA), "\n")
print(fit$parFixedDf)
write.csv(cbind(Parameter=rownames(fit$parFixedDf), fit$parFixedDf), file.path(TAB,"pk_parameters.csv"), row.names=FALSE)

# ---- GOF ----
df <- as.data.frame(fit)
gof <- function(x,y,xl,yl){ ggplot(df,aes(.data[[x]],.data[[y]]))+geom_point(alpha=.5,color="#0279EE")+
  geom_abline(slope=1,intercept=0,color="#D4A04A")+labs(x=xl,y=yl) }
res <- function(x,xl){ ggplot(df,aes(.data[[x]],CWRES))+geom_point(alpha=.5,color="#0279EE")+
  geom_hline(yintercept=c(-2,0,2),linetype=c(3,1,3),color="#D4A04A")+labs(x=xl,y="CWRES") }
library(patchwork)
g <- (gof("PRED","DV","Population predicted","Observed") | gof("IPRED","DV","Individual predicted","Observed")) /
     (res("TIME","Time (h)") | res("PRED","Population predicted"))
ggsave(file.path(FIG,"fig_pk_gof.png"), g, width=9, height=7, dpi=150)
ggsave(file.path(FIG,"fig_pk_gof.svg"), g, width=9, height=7)

# ---- VPC (default tier; simulation-only) ----
vpc_ok <- tryCatch({ v <- vpcPlot(fit, n=200, show=list(obs_dv=TRUE)); 
  ggsave(file.path(FIG,"fig_pk_vpc.png"), v, width=7, height=5, dpi=150); TRUE }, error=function(e){
  cat("VPC skipped:", conditionMessage(e), "\n"); FALSE })
cat("\nPK stage done. VPC:", vpc_ok, " Allometric:", ALLO, "\n")

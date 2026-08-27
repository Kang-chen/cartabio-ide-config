#!/usr/bin/env Rscript
# Stage 1 — ingest & validate a concentration-time (+/- response) dataset.
# GOLDEN RULE: validate LOUDLY, never drop rows silently. Prints a validation summary and
# writes both the tidy dataset and the summary. Sets PK_ONLY when no response endpoint exists.
# Adapt column mapping / endpoint labels to the dataset (see references/schema_contract.md).
.libPaths(c("/workspace/.Rlib", .libPaths()))
suppressPackageStartupMessages({library(dplyr)})

# ---- inputs (edit per dataset) ----
INFILE   <- Sys.getenv("PKPD_INFILE", "")            # user CSV; if "" use nlmixr2data example
EXAMPLE  <- Sys.getenv("PKPD_EXAMPLE", "warfarin")   # "warfarin" (PK+PD) or "theo" (PK-only)
OUTDIR   <- Sys.getenv("PKPD_OUTDIR", "/mnt/results/tables"); dir.create(OUTDIR, TRUE, TRUE)

# ---- load ----
if (nzchar(INFILE)) {
  raw <- read.csv(INFILE, stringsAsFactors = FALSE)
} else if (EXAMPLE == "theo") {
  library(nlmixr2data); data(theo_sd); raw <- theo_sd            # PK only
} else {
  library(nlmixr2data); data(warfarin); raw <- warfarin          # PK (cp) + PD (pca)
}
cat("Loaded", nrow(raw), "rows;", length(unique(raw[[grep('^id$', names(raw), ignore.case=TRUE)[1]]])),
    "subjects (raw).\n")

# ---- case-insensitive column mapping (extend synonyms as needed) ----
low <- tolower(names(raw))
pick <- function(cands) { i <- which(low %in% cands); if (length(i)) names(raw)[i[1]] else NA }
map <- list(
  ID   = pick(c("id","subject","subj","usubjid")),
  TIME = pick(c("time","t")),
  DV   = pick(c("dv","conc","concentration","y","obs","value")),
  AMT  = pick(c("amt","dose","dosemg")),
  EVID = pick(c("evid","event")),
  CMT  = pick(c("cmt","compartment")),
  DVID = pick(c("dvid","endpoint","analyte","ytype")),
  MDV  = pick(c("mdv")),
  WT   = pick(c("wt","weight","bw")),
  AGE  = pick(c("age")),
  SEX  = pick(c("sex","gender"))
)
cat("\n-- Column mapping --\n"); for (k in names(map)) cat(sprintf("  %-5s <- %s\n", k, map[[k]]))

req <- c("ID","TIME","DV","AMT")
missing_req <- req[vapply(map[req], is.na, logical(1))]
if (length(missing_req))
  stop("FATAL: required item(s) not found: ", paste(missing_req, collapse=", "),
       ". Columns seen: ", paste(names(raw), collapse=", "))

d <- data.frame(ID = raw[[map$ID]], TIME = as.numeric(raw[[map$TIME]]),
                DV = suppressWarnings(as.numeric(raw[[map$DV]])),
                AMT = suppressWarnings(as.numeric(raw[[map$AMT]])),
                stringsAsFactors = FALSE)
for (opt in c("EVID","CMT","DVID","MDV","WT","AGE","SEX"))
  if (!is.na(map[[opt]])) d[[opt]] <- raw[[map[[opt]]]]

# derive EVID if absent: dose row = AMT>0 and no observation
if (is.null(d$EVID)) { d$EVID <- ifelse(!is.na(d$AMT) & d$AMT > 0 & is.na(d$DV), 1L, 0L)
  cat("\nNote: EVID derived from AMT (no EVID column supplied).\n") }
# Normalize EVID conventions: NONMEM/nlmixr2 use 1/4 (dose), 101 (reset+dose) etc. Treat any
# non-zero EVID OR any AMT>0 row as a dose event; keep 0 = observation. Report the normalization.
d$is_dose <- (!is.na(d$EVID) & d$EVID != 0) | (!is.na(d$AMT) & d$AMT > 0 & (is.na(d$DV) | d$DV==0))
if (any(d$EVID != 0 & d$EVID != 1, na.rm=TRUE)) {
  cat("\nNote: non-standard EVID codes seen (",
      paste(sort(unique(d$EVID[d$EVID!=0 & d$EVID!=1])),collapse=","),
      ") -> normalized to dose events via is_dose (any EVID!=0 or AMT>0 dosing row).\n", sep="")
}
d$EVID <- ifelse(d$is_dose, 1L, 0L); d$is_dose <- NULL

# ---- LOUD validation ----
flags <- list()
flags$n_subjects   <- length(unique(d$ID))
flags$n_rows       <- nrow(d)
flags$n_dose       <- sum(d$EVID == 1, na.rm = TRUE)
flags$n_obs        <- sum(d$EVID == 0, na.rm = TRUE)
flags$missing_dv   <- sum(is.na(d$DV) & d$EVID == 0)
flags$nonpos_dv    <- sum(d$DV <= 0 & d$EVID == 0, na.rm = TRUE)
dup <- d %>% filter(EVID==0) %>% count(ID, TIME, DVID = if ("DVID" %in% names(d)) DVID else NA) %>% filter(n>1)
flags$dup_obs      <- nrow(dup)
flags$neg_time     <- sum(d$TIME < 0, na.rm = TRUE)
no_dose  <- setdiff(unique(d$ID), unique(d$ID[d$EVID==1]))
no_obs   <- setdiff(unique(d$ID), unique(d$ID[d$EVID==0]))
flags$subj_no_dose <- length(no_dose)
flags$subj_no_obs  <- length(no_obs)

# endpoint detection -> PK_ONLY?
endpoints <- if ("DVID" %in% names(d)) sort(unique(as.character(d$DVID[d$EVID==0]))) else "single"
PK_ONLY   <- length(endpoints) <= 1
flags$endpoints <- paste(endpoints, collapse=", ")
flags$PK_ONLY   <- PK_ONLY
dose_levels <- sort(unique(d$AMT[d$EVID==1]))
flags$dose_levels  <- paste(round(dose_levels,3), collapse=", ")
flags$single_level <- length(dose_levels) <= 1

cat("\n================ VALIDATION SUMMARY ================\n")
for (k in names(flags)) cat(sprintf("  %-14s : %s\n", k, flags[[k]]))
cat("  n_obs_per_endpoint:\n")
if ("DVID" %in% names(d)) print(table(d$DVID[d$EVID==0])) else cat("    (single endpoint)\n")
if (flags$missing_dv+flags$nonpos_dv+flags$dup_obs+flags$neg_time+flags$subj_no_dose+flags$subj_no_obs > 0)
  cat("  >> WARN: data-quality flags are non-zero — review before modeling (rows are NOT auto-dropped).\n")
if (PK_ONLY) cat("  >> PK_ONLY = TRUE: no distinct response endpoint -> PD & dose stages will be skipped.\n")
if (flags$single_level) cat("  >> SINGLE DOSE LEVEL: any dose recommendation is EXTRAPOLATION beyond observed data.\n")
cat("===================================================\n")

# ---- write tidy dataset + summary (never overwrite silently: versioned name optional) ----
write.csv(d, file.path(OUTDIR, "pkpd_tidy.csv"), row.names = FALSE)
writeLines(c("Validation summary:", vapply(names(flags), function(k) sprintf("%s: %s", k, flags[[k]]), "")),
           file.path(OUTDIR, "validation_summary.txt"))
saveRDS(list(data = d, flags = flags, endpoints = endpoints, PK_ONLY = PK_ONLY),
        "/workspace/pkpd_ingest.rds")
cat("\nWrote pkpd_tidy.csv + validation_summary.txt; PK_ONLY =", PK_ONLY, "\n")

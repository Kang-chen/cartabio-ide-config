#!/usr/bin/env Rscript
# =====================================================================================
# 08_validate_vs_manual.R  --  MANDATORY reconciliation vs a manual-gating export.
#
# The reference ADCC failure was invisible to every internal diagnostic and only surfaced
# when the pipeline was compared to a scientist's manual FlowJo statistics export. This script
# performs that comparison and emits a PASS / REVIEW verdict. A REVIEW verdict means: DO NOT
# trust downstream fits/abundances/report until the flagged gates are resolved -- and inspect
# the scatter/singlet gate FIRST (see references/qc_gating.md, references/validation_vs_manual.md).
#
# Two reconciliations, per sample:
#   (1) total post-QC cell count (from sce_prepped.rds) vs the manual top-level singlet/"Cells"
#       count  -- the PRIMARY over-gating detector (needs only step-01 output).
#   (2) per-population counts/percent (from an annotated abundance table) vs the manual export --
#       optional; runs when --abundance is supplied.
#
# Usage:
#   Rscript 08_validate_vs_manual.R \
#     --manual <FlowJo_export.csv|.tsv|.xlsx> \
#     --sce    /mnt/results/cyto_run/sce_prepped.rds \
#     --abundance /mnt/results/cyto_run/abundance_by_sample.csv \  # optional
#     --outdir /mnt/results/cyto_run \
#     --sample-col auto --count-col auto --pct-col auto \          # optional overrides
#     --tol-pp 5 --tol-rel 0.20
#
# Output: <outdir>/validation_vs_manual.csv, <outdir>/validation_vs_manual_log.txt, verdict on stdout.
# =====================================================================================

suppressPackageStartupMessages({ library(optparse) })

opt <- parse_args(OptionParser(option_list = list(
  make_option("--manual", type = "character"),
  make_option("--sce", type = "character", default = NA),
  make_option("--abundance", type = "character", default = NA),
  make_option("--outdir", type = "character", default = "."),
  make_option("--sample-col", type = "character", default = "auto", dest = "sample_col"),
  make_option("--count-col", type = "character", default = "auto", dest = "count_col"),
  make_option("--pct-col", type = "character", default = "auto", dest = "pct_col"),
  make_option("--tol-pp", type = "double", default = 5, dest = "tol_pp"),
  make_option("--tol-rel", type = "double", default = 0.20, dest = "tol_rel")
)))
dir.create(opt$outdir, recursive = TRUE, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "validation_vs_manual_log.txt"); cat("", file = LOG)
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
logmsg("=== 08_validate_vs_manual.R  |  %s ===", as.character(Sys.time()))

# ------------------------------------------------------------------ helpers
norm_id <- function(x) tolower(trimws(sub("\\.fcs$", "", as.character(x), ignore.case = TRUE)))

read_any <- function(path) {
  ext <- tolower(tools::file_ext(path))
  if (ext == "csv") return(read.csv(path, check.names = FALSE, stringsAsFactors = FALSE))
  if (ext %in% c("tsv", "txt")) return(read.delim(path, check.names = FALSE, stringsAsFactors = FALSE))
  if (ext %in% c("xlsx", "xls")) {
    if (!requireNamespace("readxl", quietly = TRUE))
      stop("Reading .xlsx needs the 'readxl' package. Install it, or re-export the manual stats as CSV.")
    return(as.data.frame(readxl::read_excel(path), check.names = FALSE, stringsAsFactors = FALSE))
  }
  stop("Unsupported manual export format: .", ext, " (use .csv/.tsv/.xlsx)")
}

# parse a FlowJo-style column header -> leaf population name + statistic type
classify_header <- function(h) {
  is_pct <- grepl("freq|%|percent", h, ignore.case = TRUE)
  is_cnt <- grepl("count|#|events", h, ignore.case = TRUE) ||
            grepl("(^|[^a-z])n([^a-z]|$)", h, ignore.case = TRUE)
  base <- trimws(sub("\\|.*$", "", h))          # drop the "| Freq. of Parent (%)" suffix if present
  leaf <- trimws(sub(".*/", "", base))          # take the deepest gate name
  leaf <- trimws(gsub("(freq\\.? of parent.*|freq\\.? of.*|count|%|\\(%\\)|events|\\|)", "", leaf, ignore.case = TRUE))
  stat <- if (is_pct) "pct" else if (is_cnt) "count" else NA_character_
  list(pop = leaf, stat = stat)
}

# ------------------------------------------------------------------ read + melt the manual export
if (is.na(opt$manual) || !file.exists(opt$manual)) stop("--manual export not found: ", opt$manual)
man <- read_any(opt$manual)
logmsg("Manual export: %s  (%d rows x %d cols)", opt$manual, nrow(man), ncol(man))

# sample column
if (opt$sample_col != "auto" && opt$sample_col %in% colnames(man)) {
  scol <- opt$sample_col
} else {
  # first column that looks like a text sample id (name/sample/file), else first non-numeric column
  cand <- grep("name|sample|file|specimen|well|tube", colnames(man), ignore.case = TRUE, value = TRUE)
  scol <- if (length(cand)) cand[1] else colnames(man)[which(!vapply(man, is.numeric, logical(1)))[1]]
}
if (is.na(scol)) stop("Could not identify a sample column in the manual export; pass --sample-col.")
logmsg("Sample column: '%s'", scol)

# melt every other column into (sample, population, stat, value)
long <- do.call(rbind, lapply(setdiff(colnames(man), scol), function(h) {
  cl <- classify_header(h)
  if (is.na(cl$stat)) return(NULL)
  v <- suppressWarnings(as.numeric(gsub(",", "", as.character(man[[h]]))))
  data.frame(sample = norm_id(man[[scol]]), population = cl$pop, stat = cl$stat,
             value = v, header = h, stringsAsFactors = FALSE)
}))
if (is.null(long) || nrow(long) == 0)
  stop("No Count/Percent columns recognized in the manual export; pass --count-col/--pct-col explicitly.")
logmsg("Recognized %d manual (sample x population x stat) records across %d populations.",
       nrow(long), length(unique(long$population)))

rows <- list(); flagged_any <- FALSE

# ------------------------------------------------------------------ (1) TOTAL post-QC cells vs manual singlets
if (!is.na(opt$sce) && file.exists(opt$sce)) {
  suppressPackageStartupMessages({ library(SingleCellExperiment) })
  sce <- readRDS(opt$sce)
  pipe_cells <- as.data.frame(table(sample = norm_id(sce$sample_id)), stringsAsFactors = FALSE)
  colnames(pipe_cells) <- c("sample", "pipeline_cells")
  # manual singlet/parent proxy = the LARGEST count per sample (parent gate is a superset of children)
  man_counts <- long[long$stat == "count", ]
  man_par <- aggregate(value ~ sample, data = man_counts, FUN = function(x) max(x, na.rm = TRUE))
  colnames(man_par) <- c("sample", "manual_singlets")
  m <- merge(pipe_cells, man_par, by = "sample", all = TRUE)
  logmsg("--- (1) Total post-QC cells vs manual singlet/parent count ---")
  for (i in seq_len(nrow(m))) {
    pc <- m$pipeline_cells[i]; mc <- m$manual_singlets[i]
    rel <- if (!is.na(mc) && mc > 0 && !is.na(pc)) (pc - mc) / mc else NA
    flag <- !is.na(rel) && abs(rel) > opt$tol_rel
    flagged_any <- flagged_any || isTRUE(flag)
    logmsg("  %-24s pipeline=%s manual=%s  rel.diff=%s %s",
           m$sample[i], ifelse(is.na(pc), "NA", pc), ifelse(is.na(mc), "NA", mc),
           ifelse(is.na(rel), "NA", sprintf("%+.1f%%", 100 * rel)),
           if (isTRUE(flag)) "  <-- FLAG (likely OVER-GATING if pipeline << manual: inspect scatter/singlet gate FIRST)" else "")
    rows[[length(rows) + 1]] <- data.frame(sample = m$sample[i], population = "(total singlets/cells)",
      stat = "count", pipeline = pc, manual = mc, delta = ifelse(is.na(pc) || is.na(mc), NA, pc - mc),
      rel_diff = rel, flagged = isTRUE(flag), stringsAsFactors = FALSE)
  }
} else {
  logmsg("NOTE: --sce not supplied; skipping the primary total-cell (over-gating) reconciliation.")
}

# ------------------------------------------------------------------ (2) per-population reconciliation (optional)
if (!is.na(opt$abundance) && file.exists(opt$abundance)) {
  ab <- read.csv(opt$abundance, check.names = FALSE, stringsAsFactors = FALSE)
  # loose column detection in the pipeline abundance table
  sc <- grep("sample", colnames(ab), ignore.case = TRUE, value = TRUE)[1]
  pc <- grep("pop|cluster|cell.?type|label", colnames(ab), ignore.case = TRUE, value = TRUE)[1]
  pctc <- grep("pct|percent|freq|prop", colnames(ab), ignore.case = TRUE, value = TRUE)[1]
  cntc <- grep("count|^n$|n_cell|cells", colnames(ab), ignore.case = TRUE, value = TRUE)[1]
  if (is.na(sc) || is.na(pc)) {
    logmsg("NOTE: could not identify sample/population columns in --abundance; skipping population reconciliation.")
  } else {
    logmsg("--- (2) Per-population reconciliation (pipeline abundance vs manual) ---")
    ab$.s <- norm_id(ab[[sc]]); ab$.p <- tolower(trimws(ab[[pc]]))
    long$.p <- tolower(trimws(long$population))
    pops <- intersect(unique(ab$.p), unique(long$.p))
    if (!length(pops)) logmsg("  No population names matched between pipeline and manual (supply --map).")
    for (p in pops) for (s in unique(ab$.s)) {
      arow <- ab[ab$.s == s & ab$.p == p, , drop = FALSE]; if (!nrow(arow)) next
      # percent comparison
      mp <- long[long$.p == p & long$sample == s & long$stat == "pct", "value"]
      if (!is.na(pctc) && length(mp)) {
        pv <- suppressWarnings(as.numeric(arow[[pctc]][1])); mv <- mp[1]
        d <- if (is.finite(pv) && is.finite(mv)) pv - mv else NA
        flag <- !is.na(d) && abs(d) > opt$tol_pp; flagged_any <- flagged_any || isTRUE(flag)
        logmsg("  %-20s %-22s pct pipeline=%s manual=%s  d=%s %s", s, p,
               ifelse(is.na(pv), "NA", sprintf("%.2f", pv)), ifelse(is.na(mv), "NA", sprintf("%.2f", mv)),
               ifelse(is.na(d), "NA", sprintf("%+.2f pp", d)), if (isTRUE(flag)) "  <-- FLAG" else "")
        rows[[length(rows) + 1]] <- data.frame(sample = s, population = p, stat = "pct",
          pipeline = pv, manual = mv, delta = d, rel_diff = NA, flagged = isTRUE(flag), stringsAsFactors = FALSE)
      }
    }
  }
} else {
  logmsg("NOTE: --abundance not supplied; population-level reconciliation skipped (total-cell check still applies).")
}

# ------------------------------------------------------------------ verdict + save
res <- if (length(rows)) do.call(rbind, rows) else
  data.frame(sample = character(), population = character(), stat = character(),
             pipeline = numeric(), manual = numeric(), delta = numeric(),
             rel_diff = numeric(), flagged = logical())
out_csv <- file.path(opt$outdir, "validation_vs_manual.csv")
write.csv(res, out_csv, row.names = FALSE)
logmsg("Wrote %s (%d comparison rows).", out_csv, nrow(res))

n_flag <- sum(res$flagged, na.rm = TRUE)
verdict <- if (nrow(res) == 0) "INCOMPLETE" else if (flagged_any) "REVIEW" else "PASS"
logmsg("=== VERDICT: %s  (%d of %d comparisons flagged) ===", verdict, n_flag, nrow(res))
if (verdict == "REVIEW") {
  logmsg("!! DO NOT trust downstream fits / abundances / report until the flagged gates are resolved.")
  logmsg("!! Debugging order: scatter/singlet gate FIRST, then marker-positive gate, then viability threshold.")
} else if (verdict == "PASS") {
  logmsg("Automated gating agrees with the manual export within tolerance (tol_pp=%.1f, tol_rel=%.2f).",
         opt$tol_pp, opt$tol_rel)
} else {
  logmsg("No comparisons could be made -- check the manual export columns and --sce/--abundance inputs.")
}
# write a machine-readable verdict marker for the report step
writeLines(verdict, file.path(opt$outdir, "validation_verdict.txt"))
quit(status = if (verdict == "REVIEW") 2 else 0)

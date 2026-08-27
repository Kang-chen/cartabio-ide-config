#!/usr/bin/env Rscript
# run_gsva.R -- Stage 4 of signature-response-enrichment.
#
# Score every sample in a cohort for the user's named gene signatures (+ optional
# context panel) with GSVA, after alias-mapping signature symbols to the cohort's
# feature space and reporting per-signature coverage.
#
# ENVIRONMENT: pin GSVA 1.50.5 (Bioconductor 3.18). Newer GSVA pulls SpatialExperiment
# -> magick (libmagick system lib often unavailable). 1.50.5 keeps the classic
# gsva()/gsvaParam() API used here. Source install if needed:
#   BiocManager::install(version = "3.18")             # or:
#   install.packages(
#     "https://bioconductor.org/packages/3.18/bioc/src/contrib/GSVA_1.50.5.tar.gz",
#     repos = NULL, type = "source")
#
# INPUT:
#   --expr      expression matrix CSV (genes x samples), row names = gene symbols;
#               log-normalized / log-CPM values (see --kcdf).
#   --signatures  JSON: {"SET_NAME": ["GENE1","GENE2", ...], ...}
#   --context   optional GMT file of context sets (e.g. Hallmark + Reactome immune)
#   --kcdf      "Gaussian" (default; for log-CPM / log-intensity) or "Poisson" (raw counts)
#   --min-size  minimum mapped genes per set (default 3); --max-size default 500
#   --out       output GSVA score matrix CSV (sets x samples)
#   --coverage-out  output coverage report CSV
#
# USAGE:
#   Rscript run_gsva.R --expr expr.csv --signatures sigs.json \
#       --context context.gmt --kcdf Gaussian \
#       --out gsva_scores.csv --coverage-out coverage.csv

suppressWarnings(suppressMessages({
  library(optparse); library(GSVA); library(jsonlite)
}))

opt <- parse_args(OptionParser(option_list = list(
  make_option("--expr", type = "character"),
  make_option("--signatures", type = "character"),
  make_option("--context", type = "character", default = NULL),
  make_option("--kcdf", type = "character", default = "Gaussian"),
  make_option("--min-size", type = "integer", default = 3L, dest = "min_size"),
  make_option("--max-size", type = "integer", default = 500L, dest = "max_size"),
  make_option("--out", type = "character"),
  make_option("--coverage-out", type = "character", default = NULL, dest = "cov_out")
)))

# ---- load expression ----
expr <- as.matrix(read.csv(opt$expr, row.names = 1, check.names = FALSE))
mode(expr) <- "numeric"
feat <- toupper(rownames(expr))

# ---- alias resolution --------------------------------------------------------
# Symbol harmonization, applied in three layers (best available wins), so signatures
# transfer to datasets that use older/alternate symbols:
#   (1) documented manual expansions for ambiguous *family* tokens that are not real
#       single symbols (e.g. IFNA -> IFNA1+IFNA2) -- these are one-to-many and cannot
#       come from an alias table, so they are always applied;
#   (2) if limma::alias2SymbolTable is available, map each remaining symbol that is NOT
#       already present in the cohort's feature space through the official alias table,
#       and adopt the mapped symbol only when it IS present in the feature space
#       (never silently replace a symbol that already matches);
#   (3) otherwise (or when the alias lookup fails / is ambiguous) keep the symbol as-is
#       and rely on case-insensitive matching downstream.
# Any symbol still unmatched after this is reported honestly in the coverage output.
#
# NOTE: manual expansions are a small curated list; extend `manual` per project if a
# signature uses other family tokens. alias2SymbolTable (layer 2) is what generalizes
# arbitrary single-gene aliases -- if it is unavailable, coverage may be lower on
# datasets using non-current symbols, and the coverage report will show which sets lost
# genes so you can add expansions or install limma.
.alias_fun <- tryCatch(get("alias2SymbolTable", envir = asNamespace("limma")),
                       error = function(e) NULL)

resolve_symbols <- function(genes, feat_upper) {
  g <- toupper(genes)
  manual <- list("IFNA" = c("IFNA1", "IFNA2"), "IL18R" = c("IL18R1"))
  out <- character(0)
  for (sym in g) {
    if (sym %in% names(manual)) {
      out <- c(out, manual[[sym]]); next
    }
    if (sym %in% feat_upper) {           # already matches the cohort -> keep as-is
      out <- c(out, sym); next
    }
    # symbol not directly present: try the official alias table (layer 2)
    if (!is.null(.alias_fun)) {
      mapped <- tryCatch(suppressWarnings(.alias_fun(sym, species = "Hs")),
                         error = function(e) NA_character_)
      mapped <- toupper(mapped[!is.na(mapped)])
      hit <- mapped[mapped %in% feat_upper]
      if (length(hit) >= 1) { out <- c(out, hit[1]); next }  # adopt only if present
    }
    out <- c(out, sym)                    # layer 3: keep original, report if unmapped
  }
  unique(out)
}

read_gmt <- function(path) {
  lines <- readLines(path, warn = FALSE)
  sets <- lapply(lines, function(l) {
    parts <- strsplit(l, "\t")[[1]]
    if (length(parts) < 3) return(NULL)
    list(name = parts[1], genes = toupper(parts[-c(1, 2)]))
  })
  sets <- Filter(Negate(is.null), sets)
  setNames(lapply(sets, `[[`, "genes"), vapply(sets, `[[`, "", "name"))
}

# ---- assemble gene sets ----
sigs <- fromJSON(opt$signatures, simplifyVector = FALSE)
gene_sets <- lapply(sigs, function(x) resolve_symbols(unlist(x), feat))
if (!is.null(opt$context) && file.exists(opt$context)) {
  ctx <- read_gmt(opt$context)
  gene_sets <- c(gene_sets, ctx)
}

# ---- coverage report + filter ----
# Report unmapped genes explicitly so under-mapping is never silent (a signature that
# uses aliases absent from this cohort will show its missing symbols here).
unmapped_str <- function(g) {
  miss <- g[!(g %in% feat)]
  if (length(miss) == 0) "" else paste(miss, collapse = ";")
}
cov <- data.frame(gene_set = names(gene_sets),
                  n_genes = vapply(gene_sets, length, 0L),
                  n_mapped = vapply(gene_sets, function(g) sum(g %in% feat), 0L),
                  unmapped_genes = vapply(gene_sets, unmapped_str, ""),
                  stringsAsFactors = FALSE)
cov$coverage <- sprintf("%d/%d", cov$n_mapped, cov$n_genes)
cov$kept <- cov$n_mapped >= opt$min_size
if (!is.null(opt$cov_out)) write.csv(cov, opt$cov_out, row.names = FALSE)
message(sprintf("[gsva] alias resolver: limma::alias2SymbolTable %s",
                if (is.null(.alias_fun)) "UNAVAILABLE (manual + case-insensitive only)"
                else "available (applied to symbols absent from the cohort)"))
message(sprintf("[gsva] coverage (kept sets need >= %d mapped genes):", opt$min_size))
apply(cov, 1, function(r) {
  miss <- if (nzchar(r["unmapped_genes"])) sprintf(" [missing: %s]", r["unmapped_genes"]) else ""
  message(sprintf("  %-30s %s  %s%s", r["gene_set"], r["coverage"],
                  ifelse(as.logical(r["kept"]), "kept", "DROPPED"), miss))
})

gene_sets <- gene_sets[cov$kept]
if (length(gene_sets) == 0) stop("No gene set passed the min-size filter.")

# ---- GSVA (v1.50.x classic API) ----
gp <- gsvaParam(exprData = expr, geneSets = gene_sets,
                kcdf = opt$kcdf, minSize = opt$min_size, maxSize = opt$max_size)
scores <- gsva(gp, verbose = FALSE)

write.csv(as.data.frame(scores), opt$out, row.names = TRUE)
message(sprintf("[gsva] scored %d sets x %d samples (kcdf=%s) -> %s",
                nrow(scores), ncol(scores), opt$kcdf, opt$out))

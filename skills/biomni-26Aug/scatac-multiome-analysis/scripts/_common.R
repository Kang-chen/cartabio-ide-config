# ============================================================================
# Shared helpers sourced by every stage script. Keeps scripts self-consistent:
# config loading, genome registry, logging, plotting theme, safe copy.
# ============================================================================
suppressWarnings(suppressMessages({
  if (!requireNamespace("yaml", quietly = TRUE)) install.packages("yaml", repos = "https://cloud.r-project.org")
  library(yaml)
}))

# ---- config -----------------------------------------------------------------
load_config <- function() {
  # resolution order: positional arg (Rscript XX.R config.yaml) > $CONFIG > $SCATAC_CONFIG
  a <- commandArgs(trailingOnly = TRUE)
  cfg_path <- if (length(a) >= 1 && nzchar(a[1]) && file.exists(a[1])) a[1] else
    Sys.getenv("CONFIG", unset = Sys.getenv("SCATAC_CONFIG", unset = ""))
  if (cfg_path == "" || !file.exists(cfg_path))
    stop("Provide config: Rscript <stage>.R /path/to/config.yaml  (or set CONFIG env). Copy scripts/config.example.yaml first.")
  cfg <- yaml::read_yaml(cfg_path)
  # normalize lsi_dims "2:30" -> 2:30
  d <- cfg$dimreduc$lsi_dims
  if (is.character(d)) { p <- as.integer(strsplit(d, ":")[[1]]); cfg$dimreduc$lsi_dims_vec <- p[1]:p[2] }
  cfg
}

log_msg <- function(...) cat(sprintf("[%s] ", format(Sys.time(), "%H:%M:%S")), ..., "\n")
`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

# ---- paths ------------------------------------------------------------------
ensure_dirs <- function(cfg) {
  for (p in c(cfg$project$outdir,
              file.path(cfg$project$outdir, "figures"),
              file.path(cfg$project$outdir, "tables"),
              file.path(cfg$project$outdir, "macs"),
              file.path(cfg$project$outdir, "checkpoints")))
    dir.create(p, recursive = TRUE, showWarnings = FALSE)
}
ckpt <- function(cfg, name) file.path(cfg$project$outdir, "checkpoints", name)
figp <- function(cfg, name) file.path(cfg$project$outdir, "figures", name)
tabp <- function(cfg, name) file.path(cfg$project$outdir, "tables", name)

# ---- plotting ---------------------------------------------------------------
okabe <- c("#E69F00","#56B4E9","#009E73","#F0E442","#0072B2","#D55E00","#CC79A7",
           "#000000","#999999","#8DD3C7","#BEBADA","#FB8072","#80B1D3","#FDB462")
clpal <- function(n) if (n <= length(okabe)) okabe[seq_len(n)] else grDevices::colorRampPalette(okabe)(n)
plot_theme <- function() ggplot2::theme_bw(base_size = 11) +
  ggplot2::theme(text = ggplot2::element_text(family = "Liberation Sans"))
save_fig <- function(cfg, p, name, w = 7, h = 5) {
  ggplot2::ggsave(figp(cfg, paste0(name, ".png")), p, width = w, height = h, dpi = 150, bg = "white")
  tryCatch(ggplot2::ggsave(figp(cfg, paste0(name, ".svg")), p, width = w, height = h, bg = "white"),
           error = function(e) log_msg("SVG save skipped for", name, ":", conditionMessage(e)))
}

# ---- safe copy to /mnt/results (shell cp, never R file.copy on FUSE) --------
copy_to_results <- function(cfg) {
  rd <- cfg$project$results_dir
  dir.create(file.path(rd, "figures"), recursive = TRUE, showWarnings = FALSE)
  dir.create(file.path(rd, "tables"),  recursive = TRUE, showWarnings = FALSE)
  fs <- list.files(figp(cfg, ""), full.names = TRUE)
  ts <- list.files(tabp(cfg, ""), full.names = TRUE)
  if (length(fs)) system2("cp", c(fs, file.path(rd, "figures/")))
  if (length(ts)) system2("cp", c(ts, file.path(rd, "tables/")))
  log_msg("Copied", length(fs), "figures and", length(ts), "tables to", rd)
}

# ---- genome registry: build -> validated (EnsDb, BSgenome, blacklist, gsize) -
GENOME_REGISTRY <- list(
  hg38 = list(ensdb = "EnsDb.Hsapiens.v86", bsgenome = "BSgenome.Hsapiens.UCSC.hg38",
              blacklist = "blacklist_hg38_unified", macs_gsize = "hs"),
  hg19 = list(ensdb = "EnsDb.Hsapiens.v75", bsgenome = "BSgenome.Hsapiens.UCSC.hg19",
              blacklist = "blacklist_hg19", macs_gsize = "hs"),
  mm10 = list(ensdb = "EnsDb.Mmusculus.v79", bsgenome = "BSgenome.Mmusculus.UCSC.mm10",
              blacklist = "blacklist_mm10", macs_gsize = "mm")
)

.ensure_bioc <- function(pkg) {
  if (!requireNamespace(pkg, quietly = TRUE)) {
    if (!requireNamespace("BiocManager", quietly = TRUE))
      install.packages("BiocManager", repos = "https://cloud.r-project.org")
    log_msg("Installing", pkg, "from Bioconductor (first-use; may take minutes)")
    BiocManager::install(pkg, update = FALSE, ask = FALSE)
  }
}

# Returns list(annotations=GRanges [UCSC], bsgenome=obj, blacklist=GRanges, macs_gsize=chr, build=chr)
resolve_genome <- function(build) {
  if (is.null(GENOME_REGISTRY[[build]]))
    stop("Unknown genome build '", build, "'. Supported: ", paste(names(GENOME_REGISTRY), collapse = ", "))
  reg <- GENOME_REGISTRY[[build]]
  for (p in c(reg$ensdb, reg$bsgenome)) .ensure_bioc(p)
  suppressPackageStartupMessages({
    library(reg$ensdb, character.only = TRUE); library(reg$bsgenome, character.only = TRUE)
    library(Signac); library(GenomicRanges)
  })
  ensdb_obj <- get(reg$ensdb)
  annotations <- Signac::GetGRangesFromEnsDb(ensdb = ensdb_obj)
  GenomeInfoDb::seqlevelsStyle(annotations) <- "UCSC"
  GenomeInfoDb::genome(annotations) <- build
  data(list = reg$blacklist, package = "Signac")
  blacklist <- get(reg$blacklist)
  bsg <- get(reg$bsgenome)
  # sanity: all three must be non-empty and UCSC-style
  stopifnot(length(annotations) > 0, length(blacklist) > 0,
            any(grepl("^chr", GenomeInfoDb::seqlevels(annotations))))
  log_msg("Genome resolved:", build, "| annot", length(annotations),
          "features | blacklist", length(blacklist), "regions | macs -g", reg$macs_gsize)
  list(annotations = annotations, bsgenome = bsg, blacklist = blacklist,
       macs_gsize = reg$macs_gsize, build = build)
}

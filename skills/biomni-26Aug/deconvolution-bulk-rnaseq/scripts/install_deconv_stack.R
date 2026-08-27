# =============================================================================
# Install the deconvolution stack: BayesPrism, DWLS, MuSiC, BisqueRNA, SimBu,
# plus every Bioc + CRAN dependency the chain needs.
#
# Pinned versions (validated build):
#   BayesPrism  - github:Danko-Lab/BayesPrism @ 19052052a6f30833b27e2148294459b0ba2f923e (2026-04-03)
#   MuSiC       - github:xuranw/MuSiC          @ f21fe67f5670d5e9fca0ad7550abaae3423eb59c (2024-03-04)
#   BisqueRNA   - CRAN archive: BisqueRNA_1.0.5.tar.gz (removed from current CRAN)
#   DWLS        - CRAN (depends on MAST from Bioc)
#   SimBu       - Bioconductor
#
# Reason we don't use BiocManager / remotes::install_github for BayesPrism/MuSiC:
#   - BayesPrism is not on Bioc/CRAN
#   - MuSiC is not on CRAN
#   - remotes::install_github routes through the GitHub REST API which rate-
#     limits at 60 req/h unauthenticated; codeload.github.com is the static
#     archive endpoint and is not rate-limited.
#
# Dependency cascade we have to pre-install (none of these auto-resolve from
# the tarball install path):
#   - BisqueRNA needs:  limSolve (CRAN)
#   - BayesPrism needs: snowfall, NMF (CRAN) + scran (Bioc)
#   - MuSiC needs:      nnls, MCMCpack (CRAN) + TOAST (Bioc)
# =============================================================================
n_cpu <- max(parallel::detectCores() - 1L, 1L)
options(Ncpus = n_cpu)
options(repos = c(CRAN = "https://cloud.r-project.org"))
cat("[install] R", R.version$major, ".", R.version$minor, " Ncpus=", n_cpu, "\n", sep = "")

stage <- function(label, expr) {
    cat("\n=========================================\n[install] STAGE: ", label, "\n=========================================\n", sep = "")
    t0 <- Sys.time()
    r <- tryCatch(eval.parent(substitute(expr)), error = function(e) { cat("STAGE FAILED: ", label, " -- ", conditionMessage(e), "\n"); e })
    cat("[install] stage '", label, "' done in ", format(Sys.time() - t0), "\n", sep = "")
    r
}

# --- Stage A: CRAN deps ------------------------------------------------------
stage("CRAN deps: limSolve, snowfall, NMF, nnls, MCMCpack", {
    pkgs <- c("limSolve", "snowfall", "NMF", "nnls", "MCMCpack")
    miss <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]
    if (length(miss)) {
        cat("[install] CRAN deps installing:", paste(miss, collapse = ", "), "\n")
        install.packages(miss, Ncpus = n_cpu)
    } else cat("[install] CRAN deps already present.\n")
})

# --- Stage B: Bioc deps ------------------------------------------------------
stage("Bioc deps: scran, TOAST", {
    bioc_pkgs <- c("scran", "TOAST")
    miss <- bioc_pkgs[!vapply(bioc_pkgs, requireNamespace, logical(1), quietly = TRUE)]
    if (length(miss)) {
        cat("[install] Bioc deps installing:", paste(miss, collapse = ", "), "\n")
        BiocManager::install(miss, ask = FALSE, update = FALSE, Ncpus = n_cpu)
    } else cat("[install] Bioc deps already present.\n")
})

# --- Stage C: BisqueRNA (CRAN archive) ---------------------------------------
stage("CRAN archive: BisqueRNA", {
    if (!requireNamespace("BisqueRNA", quietly = TRUE)) {
        url <- "https://cran.r-project.org/src/contrib/Archive/BisqueRNA/BisqueRNA_1.0.5.tar.gz"
        tarball <- file.path(tempdir(), "BisqueRNA_1.0.5.tar.gz")
        utils::download.file(url, tarball, mode = "wb", quiet = FALSE)
        install.packages(tarball, repos = NULL, type = "source", Ncpus = n_cpu)
    } else cat("[install] BisqueRNA already present.\n")
})

# --- Stage D: BayesPrism (codeload tarball, pinned SHA) ----------------------
BAYESPRISM_SHA <- "19052052a6f30833b27e2148294459b0ba2f923e"  # 2026-04-03, Danko-Lab/BayesPrism main
stage("Tarball: BayesPrism", {
    if (!requireNamespace("BayesPrism", quietly = TRUE)) {
        zip_url <- paste0("https://codeload.github.com/Danko-Lab/BayesPrism/tar.gz/", BAYESPRISM_SHA)
        zip_path <- file.path(tempdir(), "bayesprism_repo.tar.gz")
        utils::download.file(zip_url, zip_path, mode = "wb", quiet = FALSE)
        extract_dir <- file.path(tempdir(), "bayesprism_extract")
        dir.create(extract_dir, showWarnings = FALSE, recursive = TRUE)
        utils::untar(zip_path, exdir = extract_dir)
        repo_root <- list.files(extract_dir, full.names = TRUE)[1]
        pkg_root <- file.path(repo_root, "BayesPrism")
        if (!dir.exists(pkg_root)) stop("missing BayesPrism/ subdir under ", repo_root)
        install.packages(pkg_root, repos = NULL, type = "source", Ncpus = n_cpu)
    } else cat("[install] BayesPrism already present.\n")
})

# --- Stage E: MuSiC (codeload tarball, pinned SHA) ---------------------------
MUSIC_SHA <- "f21fe67f5670d5e9fca0ad7550abaae3423eb59c"  # 2024-03-04, xuranw/MuSiC master
stage("Tarball: MuSiC", {
    if (!requireNamespace("MuSiC", quietly = TRUE)) {
        zip_url <- paste0("https://codeload.github.com/xuranw/MuSiC/tar.gz/", MUSIC_SHA)
        zip_path <- file.path(tempdir(), "music_repo.tar.gz")
        utils::download.file(zip_url, zip_path, mode = "wb", quiet = FALSE)
        extract_dir <- file.path(tempdir(), "music_extract")
        dir.create(extract_dir, showWarnings = FALSE, recursive = TRUE)
        utils::untar(zip_path, exdir = extract_dir)
        repo_root <- list.files(extract_dir, full.names = TRUE)[1]
        install.packages(repo_root, repos = NULL, type = "source", Ncpus = n_cpu)
    } else cat("[install] MuSiC already present.\n")
})

# --- Final inventory ---------------------------------------------------------
cat("\n=========================================\n[install] FINAL INVENTORY\n=========================================\n")
for (p in c("DWLS","BayesPrism","MuSiC","BisqueRNA","SimBu",
            "zellkonverter","SingleCellExperiment","SummarizedExperiment",
            "MAST","scran","TOAST","limSolve","snowfall","NMF","nnls","MCMCpack",
            "lmerTest","ggprism","remotes","BiocManager")) {
    has <- requireNamespace(p, quietly = TRUE)
    cat(sprintf("  %-25s %s\n", p, ifelse(has, "[OK]", "[MISSING]")))
}
cat("[install] DONE\n")

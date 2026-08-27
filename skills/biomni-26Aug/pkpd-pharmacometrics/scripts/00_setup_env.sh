#!/usr/bin/env bash
# Stage 0 — install the nlmixr2 / rxode2 pharmacometric stack into /workspace/.Rlib
# The stack is NOT preinstalled in the Biomni base R environment.
# rxode2 compiles ODE models on the fly -> a C compiler (gcc/cc) + gfortran are REQUIRED.
# This install is SLOW the first time (~10-20 min). Run it in the BACKGROUND and gate modeling
# on its completion. It is idempotent: already-installed packages are skipped.
set -euo pipefail

export R_LIBS_USER=/workspace/.Rlib
mkdir -p /workspace/.Rlib

# Sanity: compilers must exist (rxode2 needs them)
command -v gcc  >/dev/null 2>&1 || command -v cc >/dev/null 2>&1 || {
  echo "ERROR: no C compiler (gcc/cc) on PATH — rxode2 cannot compile models"; exit 1; }
command -v gfortran >/dev/null 2>&1 || {
  echo "ERROR: gfortran not on PATH — rxode2/nlmixr2 need a Fortran compiler"; exit 1; }

Rscript --no-save --quiet -e '
.libPaths(c("/workspace/.Rlib", .libPaths()))
repos <- "https://cloud.r-project.org"
pkgs  <- c("rxode2", "nlmixr2est", "nlmixr2", "nlmixr2data", "vpc", "ggplot2", "ggprism")
need  <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]
if (length(need)) {
  cat("Installing:", paste(need, collapse=", "), "\n")
  install.packages(need, repos = repos, Ncpus = max(1, parallel::detectCores()-1))
} else cat("All packages already present.\n")

# Verify load + that rxode2 can COMPILE a trivial model (the real gate)
suppressPackageStartupMessages({library(rxode2); library(nlmixr2)})
cat("rxode2:", as.character(packageVersion("rxode2")),
    "| nlmixr2:", as.character(packageVersion("nlmixr2")),
    "| nlmixr2est:", as.character(packageVersion("nlmixr2est")), "\n")
m <- rxode2({ d/dt(x) <- -x })                 # compiles or errors
cat("rxode2 compile test: OK\n")
'
echo "Stage 0 complete: nlmixr2 stack ready in /workspace/.Rlib"

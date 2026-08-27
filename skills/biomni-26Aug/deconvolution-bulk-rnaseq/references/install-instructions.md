# Install instructions -- deconvolution stack

This is the install procedure that produced the validated build of this skill. The script `scripts/install_deconv_stack.R` is the executable form of these instructions.

## TL;DR

```r
# From the skill root, on a fresh R environment with internet access:
Rscript scripts/install_deconv_stack.R
```

Wallclock: ~12 minutes on a 16-CPU sandbox (first run; subsequent runs are no-ops since each stage is idempotent).

## Why this script exists

CRAN + Bioconductor don't carry every method. BayesPrism and MuSiC are GitHub-only; BisqueRNA was removed from current CRAN and has to come from the CRAN archive. The naive `BiocManager::install()` + `remotes::install_github()` recipe fails on a clean machine for three independent reasons:

1. **Dependency cascade.** None of the three GitHub/archive installs auto-resolve their dependencies:
   - BisqueRNA needs `limSolve` (CRAN)
   - BayesPrism needs `snowfall`, `NMF` (CRAN) plus `scran` (Bioc)
   - MuSiC needs `nnls`, `MCMCpack` (CRAN) plus `TOAST` (Bioc)
2. **GitHub API rate limit.** `remotes::install_github()` routes through the REST API, capped at 60 requests/hour unauthenticated. A few retries during dependency resolution exhausts this on a shared sandbox.
3. **BisqueRNA off CRAN.** The latest version (1.0.5) only lives at `https://cran.r-project.org/src/contrib/Archive/BisqueRNA/BisqueRNA_1.0.5.tar.gz`.

The script handles all three: pre-installs CRAN+Bioc deps, then bypasses the GitHub REST API by downloading the source tarballs directly from `codeload.github.com`, then installs BisqueRNA from the CRAN archive URL.

## Pinned versions

Reproducibility requires SHA pins -- neither BayesPrism nor MuSiC publishes tagged releases.

| Package | Source | Pin |
|---|---|---|
| BayesPrism | `github:Danko-Lab/BayesPrism` (main HEAD) | `19052052a6f30833b27e2148294459b0ba2f923e` (2026-04-03) |
| MuSiC | `github:xuranw/MuSiC` (master HEAD) | `f21fe67f5670d5e9fca0ad7550abaae3423eb59c` (2024-03-04) |
| BisqueRNA | CRAN archive | `BisqueRNA_1.0.5.tar.gz` |
| DWLS | CRAN | latest (depends on MAST from Bioc) |
| SimBu | Bioconductor | latest matching the installed BioC release |

The script reads the two SHAs from named constants near the top (`BAYESPRISM_SHA`, `MUSIC_SHA`); bump those values to update.

## Stages

The script runs five idempotent stages and prints `[install] STAGE: <name>` boundaries:

1. **CRAN deps.** `limSolve`, `snowfall`, `NMF`, `nnls`, `MCMCpack`. ~3 min on first run (NMF is the big one).
2. **Bioc deps.** `scran`, `TOAST`. ~5 min on first run (scran has a deep transitive set).
3. **BisqueRNA.** CRAN archive download + source install. ~30 s.
4. **BayesPrism.** Codeload tarball from the pinned SHA -> extract -> install from `BayesPrism/` subdirectory. ~30 s.
5. **MuSiC.** Codeload tarball from the pinned SHA -> install. ~30 s.

After all stages, the script prints a final inventory of every package + `[OK]`/`[MISSING]` status.

## When things go wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| `installation of package 'X' had non-zero exit status` after Stage 1 finishes | Bioc release doesn't match the installed R version | `BiocManager::install(update = FALSE, version = "3.20")` then re-run |
| Stage 4 fails with `missing BayesPrism/ subdir under <root>` | Codeload URL returned an empty/redirect tarball | Re-check the SHA URL resolves: `curl -sI https://codeload.github.com/Danko-Lab/BayesPrism/tar.gz/<SHA>` should be `HTTP 200` |
| `403` from any codeload URL | IP-level GitHub block (rare on shared sandboxes) | Wait an hour or rotate to a different sandbox |
| `snowfall` or `scran` install hangs | Bioc mirror flaky | Set `options(BioC_mirror = "https://bioconductor.org")` then re-run |
| MuSiC install passes but `library(MuSiC)` crashes | Stale `nnls`/`MCMCpack` from an older R | `remove.packages(c("nnls","MCMCpack"))` then re-run |

## What the validated build contains

After a successful run, `requireNamespace()` returns `TRUE` for all of:

```
DWLS, BayesPrism, MuSiC, BisqueRNA, SimBu,
zellkonverter, SingleCellExperiment, SummarizedExperiment,
MAST, scran, TOAST,
limSolve, snowfall, NMF, nnls, MCMCpack,
lmerTest, ggprism, remotes, BiocManager
```

## Verifying the pinned URLs resolve

If you bump `BAYESPRISM_SHA` or `MUSIC_SHA`, sanity-check the URLs respond with `HTTP 200`:

```bash
curl -sI "https://codeload.github.com/Danko-Lab/BayesPrism/tar.gz/<BAYESPRISM_SHA>" | head -1
curl -sI "https://codeload.github.com/xuranw/MuSiC/tar.gz/<MUSIC_SHA>" | head -1
```

A `HTTP 404` means the SHA is wrong or the branch was force-pushed.

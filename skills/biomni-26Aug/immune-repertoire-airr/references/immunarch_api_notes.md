# immunarch 0.9.1 — verified API notes, install gotcha, and modality rationale

These notes are ground-truth, captured by directly probing immunarch 0.9.1 on real
10x TCR data. Encode them exactly; do not guess return structures.

---

## 1. Installation (READ FIRST — this is the #1 time sink)

**Install immunarch 0.9.1 (classic in-memory loader, NO duckdb):**

```r
.libPaths("/workspace/.Rlib")                 # per-machine local lib
options(repos = "https://cloud.r-project.org", Ncpus = 8)
Sys.setenv(MAKEFLAGS = "-j8")
install.packages("ggraph")                     # dependency that fails if skipped
if (!requireNamespace("remotes", quietly = TRUE)) install.packages("remotes")
remotes::install_version("immunarch", version = "0.9.1",
                         dependencies = NA, upgrade = "never")
```

**Why not the current CRAN immunarch?** The current version depends on
`immundata` -> `duckdb`. `duckdb` compiles from source and is pathologically slow
(>40 min even at `-j8`; effectively hangs on a 1-CPU box). **None** of the four
analyses (clonality, diversity, gene usage, overlap) need duckdb.

**Why `dependencies = NA` (not FALSE)?** `dependencies = FALSE` skips ~18 light
pure-R deps that 0.9.x genuinely needs and the install fails. `NA` installs
Depends/Imports/LinkingTo only (no Suggests) — fast and complete.

**Compute:** provision an 8-core machine with `ManageMachine` for the install
(worker-0 is 1 CPU and too slow). The install takes ~2-5 min at 8 cores after
`ggraph`. The analysis itself is light (seconds-minutes for <=20 samples).

**Verify load:**
```r
library(immunarch)
stopifnot(all(sapply(c("repLoad","repDiversity","repClonality","geneUsage",
                       "repOverlap","repExplore","geneUsageAnalysis"),
                     exists)))
```

---

## 2. Verified return structures (immunarch 0.9.1)

### repLoad(DATA_DIR)
- Returns a list with `$data` (named list of per-sample data.frames) and `$meta`
  (data.frame; joined from a tab-separated `metadata.txt` whose first column is
  `Sample`).
- Auto-detects 10x `filtered_contig_annotations.csv`, MiXCR, Adaptive/immunoSEQ,
  AIRR-C TSV, and immunarch-native formats.
- 10x rows are **cell-level PAIRED chains**: e.g. `V.name = "TRAV26-2;TRBV7-3"`,
  `J.name = "TRAJ24;TRBJ1-1"`, `CDR3.aa = "CICRSWGKLQF;CASSLGAPTEAFF"`.
- Key columns (32 total): `Clones`, `Proportion`, `CDR3.aa`, `V.name`, `J.name`.
- `Clones` = abundance (cells for single-cell; templates/reads for bulk).

### repClonality(.method = "top", .head = c(10,100,1000))
- Returns a **matrix** (class `immunr_top_prop`), rows = samples, cols = `"10"`,
  `"100"`, `"1000"` (fraction of repertoire occupied by the top-N clones).

### repClonality(.method = "homeo")
- Returns a **matrix**, 5 columns (clonal-space bins):
  `"Rare (0 < X <= 1e-05)"`, `"Small (1e-05 < X <= 1e-04)"`,
  `"Medium (1e-04 < X <= 0.001)"`, `"Large (0.001 < X <= 0.01)"`,
  `"Hyperexpanded (0.01 < X <= 1)"`.

### repDiversity(.method = ...)
| method       | return           | value column(s)                         |
|--------------|------------------|------------------------------------------|
| `"chao1"`    | matrix `immunr_chao1` | `Estimator`, `SD`, `Conf.95.lo`, `Conf.95.hi` |
| `"inv.simp"` | data.frame       | `Sample`, `Value`                        |
| `"gini.simp"`| data.frame       | `Sample`, `Value`                        |
| `"d50"`      | matrix `immunr_dxx`   | `Clones`, `Percentage`               |
| `"raref"`    | rarefaction object (use `.verbose=FALSE`) | long df: Sample/Size/Mean |

- **`.method = "entropy"` is INVALID** — errors with "You entered the wrong
  method!". Compute Shannon manually from `Proportion`:
  ```r
  shannon <- function(d){ p <- d$Proportion; p <- p[p>0]; -sum(p*log(p)) }
  # clonality index = 1 - Pielou evenness:
  clonality <- function(d){ p<-d$Proportion; p<-p[p>0]; if(length(p)<2) 0 else 1 - (-sum(p*log(p)))/log(length(p)) }
  ```

### Robust scalar extractor (handles both matrix and data.frame returns)
```r
scal <- function(obj, col){
  df <- as.data.frame(unclass(obj))
  v <- if ("Sample" %in% colnames(df)) setNames(df[[col]], df$Sample)
       else setNames(df[[col]], rownames(df))
  v[SAMPLE_ORDER]
}
# scal(chao,"Estimator"); scal(inv,"Value"); scal(gini,"Value"); scal(d50,"Clones")
```

### geneUsage(immdata$data, "hs.trbv", .norm = TRUE, .ambig = "exc")
- Gene-table keys: `"<species>.<chain><seg>"`, e.g. `hs.trbv`, `hs.trbj`,
  `hs.trav`, `hs.ighv`, `mm.trbv`.
- Returns a **data.frame**: col 1 = `Names` (gene), then one column per sample.
- `.ambig = "exc"` excludes ambiguous (`;`-joined) calls so paired 10x rows
  contribute only the requested chain's gene (e.g. TRBV from a TRA;TRB row).

### geneUsageAnalysis(gu, .method = "js" | "cor", .verbose = FALSE)
- Returns a similarity matrix over samples for a heatmap (`js` = Jensen-Shannon
  divergence, lower = more similar; `cor` = correlation).

### repOverlap(immdata$data, .method = "public" | "morisita", .verbose = FALSE)
- Returns a **symmetric matrix** (class `immunr_ov_matrix`), **NA on the
  diagonal**. `"public"` = count of shared clonotypes; `"morisita"` =
  Morisita-Horn abundance-weighted index.

---

## 3. Modality rationale (single-cell vs bulk) — the key scientific switch

**Detect** modality from the singleton fraction (`mean(Clones == 1)` across
samples): >= ~0.85 -> single-cell-like; lower -> bulk. Also infer from format
(10x contig CSV -> single-cell; immunoSEQ/MiXCR bulk -> bulk).

**Why it changes interpretation:**
- Chao1 extrapolates richness from the singleton/doubleton (F1/F2) ratio. In
  single-cell data nearly every clonotype is a singleton (F1 huge, F2 ~ 0), so
  Chao1 **explodes**. In one verified run, Chao1 = 3.8x10^4 to 3.3x10^5 while
  observed clonotypes were only 478-6,131. This is an artifact, not richness.
- On **bulk** data (e.g. immunoSEQ) there is a real abundance distribution with
  genuine doubletons, so Chao1 behaves properly and clonal expansion is real.

**Behavior switch to implement:**
- *single_cell*: flag Chao1 as an upper-bound artifact; rank samples by evenness
  indices (Shannon, inverse Simpson, Gini-Simpson, D50) + rarefaction; frame
  "clonality" as clonal-space homeostasis / top-clone occupancy, NOT expansion
  magnitude; abundance = cells.
- *bulk*: trust Chao1/richness; report clonal expansion (top-clone fractions,
  hyperexpanded bin) as genuine biology; abundance = templates/reads;
  depth-normalize before cross-sample richness comparison.

---

## 4. TCR vs BCR

- Chain-agnostic math: all four analyses run on TRB/TRA/TRG/TRD or IGH/IGK/IGL.
- Default single-chain: **TRB** (TCR) / **IGH** (BCR).
- **BCR caveats (state explicitly):** somatic hypermutation means an exact-CDR3
  clonotype is NOT a clonal lineage; isotype/class-switch structure exists and is
  not modeled. Full SHM lineage reconstruction (Change-O / Immcantation) is a
  separate, heavier workflow and is OUT OF SCOPE. Never silently label exact-CDR3
  matches "clonal lineages" for BCR.

---

## 5. FUSE / filesystem gotchas

- R's `file.copy()` to `/mnt/results` (S3 FUSE) produces **0-byte files**.
  Save PNGs to local `/workspace` first, then `system2("cp", ...)` to
  `/mnt/results`. SVG (plain text) can be written directly to `/mnt/results`.
- Random-access binary formats (.h5, .h5ad, .sqlite, .xlsx, ...) must be written
  to `/workspace` then copied. CSV/TSV/JSON/PNG/SVG write directly.

---

## 6. Verified example finding (sanity anchor)

On a 5-sample 10x TCR cohort, `repOverlap(public)` returned 202 shared clonotypes
for one pair vs <= 14 for all others. Those two samples were the **same donor**
sequenced with two chemistries (`vdj_v1_hs_pbmc3`, `vdj_nextgem_hs_pbmc3`). The
overlap analysis correctly isolated the same-source pair from unrelated-donor
background — a built-in positive control. The `overlap_flagged_pairs.csv` output
generalizes this (flags pairs with shared >> background).

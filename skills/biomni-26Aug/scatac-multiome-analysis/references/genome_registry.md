# Genome registry

The genome build selects **three coupled resources** that must agree. A mismatch
among them does not error loudly — it **silently corrupts** TSS enrichment
(wrong TSS coordinates), blacklist filtering (wrong regions removed), and any
downstream motif/coordinate work. The pipeline therefore resolves all three from
one `genome.build` key and asserts they load and share seqlevel style before use.

## The triad

| `build` | EnsDb annotation | BSgenome | ENCODE blacklist (Signac) |
|---------|------------------|----------|---------------------------|
| `hg38` (default) | `EnsDb.Hsapiens.v86` | `BSgenome.Hsapiens.UCSC.hg38` | `blacklist_hg38_unified` |
| `hg19` | `EnsDb.Hsapiens.v75` | `BSgenome.Hsapiens.UCSC.hg19` | `blacklist_hg19` |
| `mm10` | `EnsDb.Mmusculus.v79` | `BSgenome.Mmusculus.UCSC.mm10` | `blacklist_mm10` |

`Signac` ships the blacklist objects (`data(...)`); the EnsDb and BSgenome
packages install from Bioconductor on first use.

## Effective genome size (for MACS3 `-g`)

| organism | value | MACS shorthand |
|----------|-------|----------------|
| human (hg38/hg19) | 2.7e9 | `hs` |
| mouse (mm10) | 1.87e9 | `mm` |

`peaks.effective_genome_size: auto` maps human→`hs`, mouse→`mm`.

## Seqlevel style — always coerce to UCSC

10x (and most deposited) fragments use UCSC-style chromosome names (`chr1`, `chr2`,
…). EnsDb returns Ensembl style (`1`, `2`, …). The pipeline coerces annotation to
UCSC and stamps the genome so everything matches the fragments:

```r
annotations <- GetGRangesFromEnsDb(ensdb = ensdb_obj)
seqlevelsStyle(annotations) <- "UCSC"
genome(annotations) <- build          # "hg38" / "hg19" / "mm10"
Annotation(obj) <- annotations
```

If a dataset uses Ensembl-style fragment chrom names instead, coerce the fragments
side (or set `seqlevelsStyle(annotations) <- "Ensembl"`) — but be consistent, and
keep the blacklist in the same style.

## Adding a new build

Add one row to the `GENOME_REGISTRY` list in `scripts/01_ingest_qc.R`:

```r
GENOME_REGISTRY[["hg38"]] <- list(
  ensdb     = "EnsDb.Hsapiens.v86",
  bsgenome  = "BSgenome.Hsapiens.UCSC.hg38",
  blacklist = "blacklist_hg38_unified",
  macs_gsize = "hs"
)
```

The loader `resolve_genome(build)` `requireNamespace()`-installs missing packages,
loads the blacklist via `data()`, and returns the resolved objects. No other script
needs editing — they all consume the resolved triad written to the stage checkpoint.

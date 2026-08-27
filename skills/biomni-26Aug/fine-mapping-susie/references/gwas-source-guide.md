# GWAS summary-statistics sources: fetching, access, and honesty

Fine-mapping needs **full genome-wide (or at least full-region) summary statistics** — every variant's
beta, se, and position in the target window — not just the lead SNPs from a GWAS Catalog associations
table. This guide covers where to get them, how `fetch_gwas_catalog.py` behaves, and the non-negotiable
honesty rule for gated data.

## What "full summary statistics" means

- **Sufficient:** a file (usually harmonised TSV) with one row per variant carrying effect allele, other
  allele, beta (or OR→log-odds), se, p-value, chrom, position. This is what SuSiE needs.
- **NOT sufficient:** the GWAS Catalog *associations* download (only genome-wide-significant lead SNPs),
  a list of loci from a paper table, or a locus-plot image. You cannot fine-map from these.

The GWAS Catalog marks studies that have deposited full stats with the flag **`fullPvalueSet = true`**.
This flag is the gate.

## `fetch_gwas_catalog.py` behavior

```
python fetch_gwas_catalog.py --accession GCST006867 \
    --region 1:213900000-214050000 --out gwas_window.tsv --report fetch_report.json
```

Steps it performs:
1. Query the study record: `https://www.ebi.ac.uk/gwas/rest/api/studies/{accession}`.
2. **Honesty gate:** read `fullPvalueSet`.
   - **`true`** → construct the harmonised FTP path and download, subset to `--region`, write the file,
     compute an md5, and print an ancestry guess derived from `initialSampleSize` / `ancestries[]`.
   - **`false`** → **do not fabricate a file.** Print the access route (see below), write a report with
     `status = "GATED_OR_METADATA_ONLY"`, and exit with code **3**. The pipeline stops here on purpose.
3. Report the ancestry mapping so the caller can set `--ancestry`/`--superpop` for LD (never assume EUR).

### Harmonised FTP path construction

```
FTP base : https://ftp.ebi.ac.uk/pub/databases/gwas/summary_statistics
range dir: GCST{lo}-GCST{hi}   where lo = ((n-1)//1000)*1000 + 1 ; hi = lo + 999
           e.g. GCST006867 -> GCST006001-GCST007000
full path: {FTP_BASE}/{range_dir}/{accession}/harmonised/{pubmed}-{accession}-{EFO}.h.tsv.gz
           e.g. .../GCST006867/harmonised/30054458-GCST006867-EFO_0001360.h.tsv.gz
```
The harmonised (`*.h.tsv.gz`) files are aligned to a common build (GRCh38) with standardized `hm_*`
columns (`hm_chrom`, `hm_pos`, `hm_beta`, `hm_effect_allele`, ...), which downstream ingest expects.

### Ancestry mapping (free-text → 1000G superpop)

The script maps GWAS Catalog free-text ancestry to superpops (and returns a `;`-joined value when the
study is multi-ancestry, so the caller must decide):

| superpop | matches (substring, case-insensitive) |
|---|---|
| EUR | european, white, ceu, finnish |
| EAS | east asian, chinese, japanese, korean, han |
| AFR | african, african american, yoruba, afro |
| SAS | south asian, indian, pakistani, bangladeshi, punjabi |
| AMR | hispanic, latino, admixed american, amerindian |

Example: GCST006867 `initialSampleSize` = "61,714 European ancestry cases, 1,178 Pakistani ancestry
cases, ..." → guess **`EUR;SAS`**. The script flags this as mixed; the user confirms which stratum to
fine-map (see `ancestry-ld-guide.md` on multi-ancestry handling).

## When the data is gated (the fallback)

Many high-value GWAS (e.g. **DIAMANTE** T2D, Mahajan et al. 2018/2022, GCST90132184) have
`fullPvalueSet = false` because full stats sit behind a **data-use / registered-access agreement**.
The correct response is **not** to invent data or silently substitute — it is to document the access
route and either obtain the data properly or choose an open alternative *with the user's approval*.

Access routes to document / pursue when gated:
1. **The consortium's own portal.** DIAMANTE stats are distributed via the DIAGRAM/AMP-T2D portals with
   a data agreement. Follow the stated process; download the file; then run this pipeline pointing
   `--sumstats` at the obtained file (skip the fetch step).
2. **Controlled-access archives** (EGA, dbGaP) for studies deposited there — requires an approved DAR.
3. **Author request** where neither applies.
4. **An open, comparable substitute.** If access can't be obtained in time, pick an open study of the
   same trait/ancestry with `fullPvalueSet = true`, and **state the substitution explicitly** in the
   report. Worked anchor: for the PROX1 T2D locus, DIAMANTE (gated) was replaced — with user approval —
   by **Xue et al. 2018 (GCST006867)**, open, harmonised GRCh38, N = 655,666. The credible set
   (rs340874, PIP 0.96) reproduced cleanly, but the substitution is recorded, not hidden.

### The rule, stated plainly

> If summary statistics are gated, the pipeline **stops and reports the access route**. It never
> fabricates, simulates, or silently swaps datasets. Any substitution is an explicit, user-approved
> decision recorded in the study config and the report.

## Other summary-statistics sources (point `--sumstats` at the file after your own download)

- **GWAS Catalog harmonised FTP** — automated here for open studies. Broadest coverage.
- **Consortium portals** — DIAGRAM/AMP-T2D (T2D), GIANT (anthropometric), PGC (psychiatric),
  CARDIoGRAM (CAD), GLGC (lipids), etc. Often the authoritative full-resolution source; access varies.
- **Neale Lab / Pan-UKBB** — UK Biobank GWAS across thousands of phenotypes, per-ancestry in Pan-UKBB
  (useful because ancestry strata come with matched LD).
- **FinnGen** — Finnish-isolate GWAS, open summary stats (note Finnish LD ≠ general EUR; use FIN-matched
  LD where possible).
- **deCODE, BioBank Japan, Million Veteran Program** — large cohorts, access terms vary.

Whatever the source, once you have the file: run `ingest_sumstats.py` → `detect_build_liftover.py` →
build ancestry-matched LD → `run_susie_finemap.R`. The fetch step is a convenience for open GWAS
Catalog studies only; the rest of the pipeline is source-agnostic.

## References / endpoints

- GWAS Catalog REST: https://www.ebi.ac.uk/gwas/rest/api/studies/{accession}
- Harmonised summary statistics FTP: https://ftp.ebi.ac.uk/pub/databases/gwas/summary_statistics
- GWAS Catalog summary-statistics documentation: https://www.ebi.ac.uk/gwas/docs/summary-statistics

# Troubleshooting

Symptom → cause → fix, for the whole fine-mapping pipeline. Ordered roughly by how often each bites.

## Fetch / data access

**`fetch_gwas_catalog.py` exits with code 3, no file written.**
- *Cause:* the study has `fullPvalueSet = false` — full summary statistics are gated. This is intended
  behavior, not a bug.
- *Fix:* obtain the data via the access route the script printed (consortium portal / EGA / dbGaP /
  author), then rerun the pipeline with `--sumstats` pointing at the downloaded file (skip fetch). Or,
  with the user's explicit approval, substitute an open study of the same trait/ancestry and record the
  substitution. See `gwas-source-guide.md`.

**Harmonised file 404 / not found for an open study.**
- *Cause:* wrong range-directory math, or the study deposited stats in a non-harmonised layout.
- *Fix:* verify the range dir (`GCST{lo}-GCST{hi}`, lo = ((n−1)//1000)*1000+1). Check the study's FTP
  folder for a `harmonised/` subdir; some older depositions only have the author-submitted file — use
  that and run it through `ingest_sumstats.py` + `detect_build_liftover.py`.

**Downloaded window has 0 or very few variants.**
- *Cause:* region given in the wrong build, wrong chromosome format, or coordinates outside the file.
- *Fix:* confirm the region is in the file's build (harmonised = GRCh38). `fetch_gwas_catalog.py` accepts
  `--region chr:start-end` with **no** `chr` prefix on the chromosome (e.g. `1:213900000-214050000`).

## Build / harmonization

**Positions don't line up with the LD panel; huge fraction of variants dropped at LD intersection.**
- *Cause:* build mismatch (GWAS in GRCh37, panel/annotation in GRCh38).
- *Fix:* run `detect_build_liftover.py` so sumstats and LD panel share one assembly before building LD.

**Many `kriging_rss` outliers concentrated at palindromic SNPs.**
- *Cause:* strand/allele ambiguity at A/T and C/G variants.
- *Fix:* rely on the harmonization in `ld_utils.py` (orients to effect allele, flags palindromes);
  consider excluding ambiguous palindromes in the fine-mapped window if outliers persist.

## LD matrix

**`run_susie_finemap.R` errors: "only N variants shared between sumstats and LD".**
- *Cause:* id mismatch (rsID vs chr:pos:ref:alt), build mismatch, or region mismatch between the LD build
  and the sumstats.
- *Fix:* ensure `--id-col` matches the id used in `ld_snps.txt` (default `snp`). Rebuild LD for the same
  region and build as the sumstats. Confirm `ld_utils.py` produced a harmonized table with `snp, varid,
  effect_allele, other_allele`.

**High `estimated_s` (> 0.1–0.2), loud CAUTION in the log.**
- *Cause:* LD reference doesn't match GWAS ancestry (most common), or residual build/allele issues.
- *Fix:* use an ancestry-matched panel (or in-sample LD). Do **not** trust the credible set until s is
  low with a *correctly matched* panel. See `ancestry-ld-guide.md`. Never panel-shop to minimize s.

**LD matrix not symmetric error / warning.**
- *Cause:* tiny floating-point asymmetry from PLINK export (~1e-16).
- *Fix:* handled automatically — the script symmetrizes `(R+Rᵀ)/2` and reports `ld_asymmetry_fixed`.
  A *large* asymmetry, however, indicates a malformed matrix — regenerate it.

**`R` full of positive values only / results look wrong.**
- *Cause:* you passed **r²** instead of signed **r**.
- *Fix:* SuSiE needs signed correlation oriented to the effect allele. Use `ld_utils.py` output; r² is
  only for figure coloring.

## SuSiE fit

**`susie_converged = FALSE`.**
- *Cause:* LD mismatch, L too large, or a degenerate/too-wide window.
- *Fix:* fix LD ancestry/build first; then reduce `--L`; then narrow the window to the LD block around
  the lead signal.

**0 credible sets returned.**
- *Cause:* genuinely weak/absent signal in the window, too few variants, or LD mismatch suppressing the
  signal.
- *Fix:* if you expected a hit, check `estimated_s` and the window definition (is the lead variant even
  inside `--region`?). If the window is truly null, 0 sets is the correct answer.

**A credible set with many variants, low `within_cs_min_abs_corr`.**
- *Cause:* possibly a spurious component, or genuinely unresolvable LD.
- *Fix:* inspect diagnostics; a low-purity set near `min_abs_corr` is suspect. A high-purity multi-variant
  set is a real result — the signal is localized to an LD-tied cluster; report it as such, don't claim a
  single causal variant.

**Fine-mapping is very slow / memory-heavy.**
- *Cause:* window too large (> ~10,000 SNPs) or MHC.
- *Fix:* focus the window (±100–500 kb around the lead). For MHC, expect unreliable results and interpret
  with caution.

## Annotation

**GTEx layer returns nothing.**
- *Cause:* variant is genuinely not a significant eGene in that tissue (the filtered endpoint returns
  significant only), OR wrong variant id / unversioned gencodeId / non-b38 build.
- *Fix:* confirm the `chr{c}_{pos}_{ref}_{alt}_b38` id and that the build is GRCh38. If the id is right,
  an empty result is a **true negative** — report it as such.

**eQTL Catalogue layer returns nothing for a small islet cohort.**
- *Cause:* underpowered cohort or variant absent from the dataset.
- *Fix:* report as **inconclusive**, not as "no effect." Consider a larger relevant dataset if one exists.

**ENCODE SCREEN layer skipped / no response.**
- *Cause:* the beta API (`screen-beta-api.wenglab.org`) is frequently down.
- *Fix:* rerun later, or consult the UCSC ENCODE cCRE track manually. The pipeline continues without it
  by design — an outage is not "no regulatory element."

## Report / figures

**Raw HTML entities (`&gt;`, `&times;`, `<sup>`) show literally in PDF table cells.**
- *Cause:* ReportLab `Table` cells do not parse inline markup unless each cell is a `Paragraph`.
- *Fix:* already handled — `generate_finemap_report.py`'s `tbl()` wraps every cell in a `Paragraph`.
  If you add new tables, use `tbl()`; never pass raw strings with markup straight into a `Table`.

**Figure caption orphaned onto the next page / blank figure page.**
- *Cause:* image + caption split across a page break.
- *Fix:* already handled — figures are wrapped in `KeepTogether([Image(...), caption])` and sized ≤ 4.0
  inch. Keep that pattern for any figure you add.

**Overly long unrounded numbers (e.g. z = 2.6449503898...) in the report.**
- *Cause:* raw float from an API written straight to prose.
- *Fix:* use the `rnd()` helper in `generate_finemap_report.py` (2–3 sig figs) for display values.

**Figure x-axis missing numeric Mb tick labels, or annotations overlap.**
- *Cause:* offset formatting / crowded annotations.
- *Fix:* the figure script sets `ticklabel_format(useOffset=False, style='plain')` and offsets the lead
  label; if a specific locus still crowds, widen `figsize` or `--pad`. Always run the media-output check
  on the PNG and regenerate if it flags issues.

## General

**Recursive `find /mnt ...` is blocked.**
- *Cause:* platform disallows recursive find on S3-backed mounts.
- *Fix:* `ls` a specific directory, or use `/workspace` for recursive search.

**Where do outputs go?** Write random-access/binary formats (`.rds` is fine; but `.h5`, `.sqlite`, etc.)
to `/workspace` first, then copy to `/mnt/results`. CSV/TSV/JSON/PNG/SVG/PDF write directly to
`/mnt/results`.

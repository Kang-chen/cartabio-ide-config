# CRISPR Screen Reanalysis Reference (MAGeCK)

> **Native-first.** Use Biomni-native resources for discovery, metadata, and
> retrieval before raw CLI tools. Confirm packages with imports and the `mageck`
> CLI with `command -v` (`GEOparse`, `pandas`, and `mageck` are expected;
> `gseapy` may need `uv pip install gseapy`).

## Step 0: confirm the screen design with the literature

Before reanalysis, use **`LiteratureSearch`** to pull the primary paper and confirm
the experimental design you are about to encode: selection phenotype (here CFSE
proliferation), which fraction is the "treatment", donor structure, and the guide
library. Cite it in the report Methods with inline `[N]`. Getting the contrast
direction wrong silently inverts every hit, so ground it in the paper rather than
assumption. (Worked example: Shifrut et al. 2018 SLICE screen.)

## Getting the data (native-first)

**Metadata via GEOparse (native):** resolve the series, samples, and the
GEO->SRA linkage programmatically instead of hand-copying accessions:
```python
import GEOparse
gse = GEOparse.get_GEO("GSE119450", destdir="/workspace")
# inspect gse.gsms for sample titles, characteristics, and SRA/SRX links
```
For the Shifrut SLICE screen the chain is GEO **GSE119450** = SRA **SRP158611** =
BioProject **PRJNA489369**.

**Raw reads from SRA:** there is no native FASTQ-download tool, so sra-tools is the
correct fallback:
```bash
prefetch SRR7741069 SRR7741070 SRR7741071 SRR7741072
fasterq-dump --split-files SRR774106X && gzip *.fastq
```

Map SRR → sample from the GEOparse metadata. Shifrut pilot (CFSE proliferation,
2 donors):

| SRA run | Donor | Fraction | MAGeCK role |
|---|---|---|---|
| SRR7741071 | Donor 1 | CFSE-low (Div) | treatment |
| SRR7741072 | Donor 1 | CFSE-high (NonDiv) | control |
| SRR7741069 | Donor 2 | CFSE-low (Div) | treatment |
| SRR7741070 | Donor 2 | CFSE-high (NonDiv) | control |

## Contrast logic (CFSE proliferation screen)

- **treatment = CFSE-low = dividing ("Div")**; **control = CFSE-high = non-dividing
  ("NonDiv")**.
- Guide ENRICHED in Div → its knockout ENHANCES proliferation → gene is a **brake**
  (positive selection, `pos|*`).
- Guide DEPLETED in Div → knockout IMPAIRS proliferation → gene is a **positive
  effector / essential** (negative selection, `neg|*`).

## Preparing reads for MAGeCK

If you reconstructed the library from reads (see
`read_driven_library_reconstruction.md`), the cleanest path is to write processed
FASTQ containing just the 20-nt spacer in the reference orientation (reverse-
complement each read's extracted spacer if the reads were RC of the library), then
let `mageck count` auto-detect a 20-nt guide length. Do NOT pass `--trim-5` /
`--sgrna-len` when reads are already bare 20-nt spacers — it causes 0% mapping.

## MAGeCK commands (exact, v0.5.9.5)

Library file `pilot_library.csv` = `sgRNA,sequence,gene` with NO header.

```bash
# Count (auto-detect guide length from bare 20-nt spacer reads)
mageck count \
  --list-seq pilot_library.csv \
  --fastq D1_Div.fastq.gz D1_NonDiv.fastq.gz D2_Div.fastq.gz D2_NonDiv.fastq.gz \
  --sample-label D1_Div,D1_NonDiv,D2_Div,D2_NonDiv \
  --output-prefix pilot

# Test — primary: NTC normalization + null model (STAR Methods)
mageck test \
  --count-table pilot.count.txt \
  --treatment-id D1_Div,D2_Div \
  --control-id   D1_NonDiv,D2_NonDiv \
  --control-sgrna ntc_guides.txt \
  --norm-method control \
  --output-prefix pilot_div_vs_nondiv

# Test — sensitivity: median-ratio normalization
mageck test \
  --count-table pilot.count.txt \
  --treatment-id D1_Div,D2_Div \
  --control-id   D1_NonDiv,D2_NonDiv \
  --norm-method median \
  --output-prefix pilot_div_vs_nondiv_medianNorm
```

## QC thresholds

From `*.countsummary.txt`:
- **Mapping rate**: ~70–80% is typical for pooled screens (Shifrut: 74–76%).
- **Zero-count guides**: should be very few (0–2 here).
- **Gini index**: < ~0.1 indicates an even, high-complexity library (0.044–0.051
  here = excellent, no bottlenecking).
- **NTC centering**: median NTC log2FC ≈ 0 (here −0.01) confirms good
  normalization. Watch for individual NTC outliers when plotting.

## Output column reference

`gene_summary` columns:
`id, num, neg|score, neg|p-value, neg|fdr, neg|rank, neg|goodsgrna, neg|lfc,
pos|score, pos|p-value, pos|fdr, pos|rank, pos|goodsgrna, pos|lfc`.
Note **`pos|lfc == neg|lfc` = gene-median LFC**. `neg` = depleted in treatment
(Div); `pos` = enriched in treatment (Div).

`sgrna_summary` columns:
`sgrna, Gene, control_count, treatment_count, control_mean, treat_mean, LFC,
control_var, adj_var, score, p.low, p.high, p.twosided, FDR, high_in_treatment`.

## Expected biology (internal validation)

The reanalysis is validated if it recovers known T-cell biology:
- **Positive (brakes)**: CBLB (#1, LFC ≈ +0.55), CD5 (#4, +0.24), PTEN (#3, +0.30).
- **Negative (essential/effectors)**: CD3D (#1, LFC ≈ −0.60, the only FDR<0.05
  hit), LCP2/SLP-76 (#2), ITK (#6), IFNGR1, CD46.
- Genome-wide-only hits (NOT in a targeted pilot library): SOCS1, RASA2,
  TCEB2/ELOB, SOCS3.

## Next: validate the hits

After MAGeCK nominates hits, run the hit-validation step
(`references/hit_validation.md`, `scripts/depmap_crosscheck.py`) to flag which
hits are broadly essential vs context-specific, and (optionally) corroborate with
`LiteratureSearch` on each top hit's known T-cell role. Feed the validated,
verified numbers into the report and its summary infographic
(`references/reporting.md`).

## Per-donor concordance (for reproducibility figure)

Compute per-donor guide LFC from normalized counts and take the gene-median:
```python
lfc_D1 = log2((D1_Div+1)/(D1_NonDiv+1))
lfc_D2 = log2((D2_Div+1)/(D2_NonDiv+1))
# gene-level = median guide LFC per gene per donor (drop Non_Targeting_Control)
```
Guide-level correlation is noise-dominated genome-wide but strengthens with
effect size: |LFC|>0.2 → r≈0.68, 93% sign-agreement; top-40 hits ≈ 90%
sign-agreement. Report concordance stratified by effect size, not a single global r.

## Statistical-power caveat

Two donors is discovery-grade and underpowered for stringent genome-wide FDR —
expect only the single strongest hit (CD3D) to clear FDR<0.05. This is exactly
what motivated the original authors' genome-wide scale-up. Present other hits as
ranked nominations supported by effect size + cross-donor concordance.

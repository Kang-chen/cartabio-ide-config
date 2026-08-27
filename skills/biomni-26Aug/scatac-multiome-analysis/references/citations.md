# Canonical references for the scATAC-seq skill

These are the method/resource citations embedded in the generated PDF report
(`REFERENCES` list in `make_report.py`). They are the primary sources for each
step of the workflow. Verified DOIs; do not fabricate additions — if the
workflow changes, re-run `LiteratureSearch` and update both this file and the
`REFERENCES` list in the report generator together.

| # | Topic | Citation | DOI |
|---|-------|----------|-----|
| 1 | Signac framework (QC, LSI, peak calling, gene activity, coverage) | Stuart T, Srivastava A, Madad S, Lareau CA, Satija R. Single-cell chromatin state analysis with Signac. *Nature Methods* 18, 1333–1341 (2021). | 10.1038/s41592-021-01282-5 |
| 2 | MACS peak calling (used via `CallPeaks`, MACS3) | Zhang Y, Liu T, Meyer CA, et al. Model-based Analysis of ChIP-Seq (MACS). *Genome Biology* 9, R137 (2008). | 10.1186/gb-2008-9-9-r137 |
| 3 | scATAC-seq + latent semantic indexing (LSI) origin | Cusanovich DA, Daza R, Adey A, et al. Multiplex single-cell profiling of chromatin accessibility by combinatorial cellular indexing. *Science* 348, 910–914 (2015). | 10.1126/science.aab1601 |
| 4 | Seurat anchor-based label transfer (optional annotation tier) | Hao Y, Stuart T, Kowalski MH, et al. Dictionary learning for integrative, multimodal and scalable single-cell analysis. *Nature Biotechnology* 42, 293–304 (2024). | 10.1038/s41587-023-01767-y |
| 5 | CellMarker 2.0 (tissue-adaptive marker sets) | Hu C, Li T, Xu Y, et al. CellMarker 2.0: an updated database of manually curated cell markers. *Nucleic Acids Research* 51, D870–D876 (2023). | 10.1093/nar/gkac947 |
| 6 | TF-IDF transformation benefit in scATAC clustering | Zandigohar M, Dai Y. Information retrieval in single-cell chromatin analysis using TF-IDF transformation methods. *IEEE BIBM* (2022). | 10.1109/bibm55620.2022.9994949 |

**Data source note.** The report also cites the *input dataset* itself (accession
from `config.yaml -> report.dataset_accession`, e.g. a GEO/10x/ArrayExpress
accession). That citation is dataset-specific and injected at render time, not
part of this fixed method list.

# Reference sequence sets (recognition features)

Small, provenance-labelled **real** peptide reference sets used by the recognition features in
`scripts/tesla_features.py`. They are used **only** as local sequence-alignment references (Biopython
Smith-Waterman, BLOSUM62). No sequence here is fabricated; if a file is missing at runtime the
corresponding feature returns `None` (real-data-only contract), never a made-up score.

## `iedb_immunogenic_9mers.txt` — foreignness reference
- **What**: experimentally validated human MHC class I **positive** T-cell epitopes, 9-mers, deduplicated.
- **Source**: IEDB (Immune Epitope Database) `query-api.iedb.org` `tcell_search`, filters
  `mhc_class=I`, `qualitative_measure=Positive*`, host = *Homo sapiens* (NCBITaxon:9606), length = 9.
- **Citation**: Vita R, et al. *The Immune Epitope Database (IEDB): 2018 update.* Nucleic Acids Res
  2019 Jan 8;47(D1):D339-D343. doi:10.1093/nar/gky1006.
- **Used for**: the **foreignness** feature — high alignment similarity of a mutant peptide to a known
  immunogenic epitope suggests TCR-recognizability (after Łuksza et al. 2017, Nature,
  doi:10.1038/nature24473, neoantigen fitness / recognition potential).

## `human_self_9mers.txt` — dissimilarity-to-self reference
- **What**: all 9-mers tiled from the canonical UniProt sequences of 12 abundant/housekeeping human
  proteins, deduplicated.
- **Accessions**: P04406 (GAPDH), P60709 (ACTB), P68104 (EEF1A1), P0DMV8 (HSPA1A), P07437 (TUBB),
  P68871 (HBB), P11142 (HSPA8), P06733 (ENO1), P62805 (H4C1), P0CG47 (UBB), P62937 (PPIA),
  P00558 (PGK1).
- **Citation**: The UniProt Consortium. *UniProt: the Universal Protein Knowledgebase in 2023.*
  Nucleic Acids Res 2023. doi:10.1093/nar/gkac1052.
- **Used for**: the **dissimilarity-to-self** feature — a mutant peptide highly similar to a self
  peptide is more likely to be tolerized; higher dissimilarity predicts immunogenicity and ICB
  response (Richman et al. 2019, Cell Systems, doi:10.1016/j.cels.2019.08.009).

## Scope / honesty
These are **compact** references (hundreds–thousands of 9-mers), sufficient to compute a transparent,
relative recognition signal inside the skill without an external BLAST install or a full-proteome
download. They are **not** a genome-scale foreignness model; the feature is a documented approximation
of the published recognition/dissimilarity concepts, labelled as such in the methods.

# sgRNA selection summary: PCSK9

**Tier used:** Option 2 (de-novo) + Rule Set 3/CFD scoring

**Why:** No validated Addgene guides for PCSK9; de-novo guides designed and scored with open-licensed Rule Set 3 (on-target) + CFD (off-target).

**Guides selected:** 3

| sgRNA_sequence       | source   | rank_or_score   | exon_or_position   | citation_or_dataset                                |
|:---------------------|:---------|:----------------|:-------------------|:---------------------------------------------------|
| CTGCTGCTGCTGCTGCTGCT | de_novo  | RS3 29.5        |                    | Rule Set 3 (rs3, DeWeirdt 2022); CFD (Doench 2016) |
| GCAGCAGCAGCAGCAGCAGC | de_novo  | RS3 42.8        |                    | Rule Set 3 (rs3, DeWeirdt 2022); CFD (Doench 2016) |
| GACGAAAGCGACAACGCGTT | de_novo  | RS3 36.1        |                    | Rule Set 3 (rs3, DeWeirdt 2022); CFD (Doench 2016) |

## Caveats
- Test 3-4 sgRNAs per gene experimentally regardless of predicted scores.
- Confirm the Cas enzyme/PAM matches your construct (SpCas9 NGG, SaCas9 NNGRRT, Cas12a TTTV).
- For Cas12a, AsCas12a and enAsCas12a are NOT interchangeable.
- On-target scores are Rule Set 3 (rs3) predictions for SpCas9; a 30-mer context (4nt+20nt+PAM+3nt) gives the most accurate score. They do not replace empirical validation.
- Off-target: CFD scores a guide against SUPPLIED candidate off-targets only. No genome-wide off-target search is performed here -- use Cas-OFFinder/CRISPOR for that.
- Validate edits (e.g., Sanger sequencing; TIDE/T7E1 for indels).

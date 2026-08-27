# Methods — TESLA-guided neoantigen prediction & prioritization

This skill turns **real somatic variants + real HLA-I genotype + real RNA-seq expression** into
a **ranked, tiered set of candidate neoantigens**, scored with the immunogenicity parameters
established by the **Tumor Neoantigen Selection Alliance (TESLA)** consortium
(Wells et al., *Cell* 2020). It is a **real-data-only** pipeline: peptide–MHC-I binding is
produced solely by **MHCflurry** run on real inputs. It never fabricates variants, peptides,
binding, expression, or scores; if a required engine or real input is missing it raises
`EngineUnavailable` and emits no numbers.

It is the companion to (and reuses the audited core of) `neoantigen-io-response`: that skill
produces a patient-level IO-response composite from clonal neoantigen burden; **this** skill
focuses on **which individual peptides to prioritize for validation**, adds **indel/frameshift
neoORF neoantigens**, an explicit **RNA-seq expression join**, the **seven TESLA features**
(a presentation axis and a recognition axis), a **tiered priority score**, and a **benchmark
against the real TESLA immunogenicity dataset**.

---

## 1. The TESLA reference (what we are operationalizing)

Wells, van Buuren, Dang, et al. assembled a global consortium in which many groups predicted
immunogenic epitopes from **shared tumor sequencing data**; **608 epitopes** were then assessed
for T-cell recognition in patient-matched samples. By integrating peptide features associated
with **antigen presentation** and **T-cell recognition**, they built a model of tumor-epitope
immunogenicity that **filtered out 98% of non-immunogenic peptides at a precision above 0.70**,
and **validated it in an independent cohort of 310 epitopes** [1]. Pipelines that prioritized
these features had superior performance.

The features that most enriched for immunogenicity fall into two groups:

**Presentation (does the peptide get to the cell surface on MHC-I?)**
1. **Binding affinity** — strong predicted peptide–MHC-I binding.
2. **Binding stability** — a stable, long-lived peptide–MHC-I complex.
3. **Tumor abundance** — the epitope's source is highly expressed (expression × mutant-allele
   fraction), so enough antigen is actually made.

**Recognition (can a T cell see it as foreign?)**
4. **Differential agretopicity** — the mutation should create or improve MHC-I binding relative
   to its wild-type counterpart (WT %rank ÷ mut %rank ≥ 1), a hallmark of a mutation-created,
   non-self epitope.
5. **Foreignness / dissimilarity-to-self** — the peptide should resemble known immunogenic
   epitopes yet differ from the self-proteome (so it is less likely to be tolerised).
6. **Hydrophobicity of the TCR-contact residues** — hydrophobic residues at TCR-facing positions
   are associated with immunogenicity.
7. **Mutation position** — the mutation should sit where a T cell can see it (a non-anchor,
   TCR-facing position rather than an MHC anchor).

This skill computes a transparent, documented sub-score for **all seven** of these features
(three presentation, four recognition) and combines them into a **tiered priority score**. The
weights are **documented, not fit to outcome data** — the goal is an auditable prioritization, not
a re-trained clinical classifier.

---

## 2. Inputs

- **Somatic VCF** — SNVs and indels. Germline/common variants are removed using the population
  allele frequency in the VCF (e.g. a gnomAD `AF` INFO/CSQ field) above a configurable floor.
  Variant consequence, gene, HGVSp/HGVSc, and (where present) VAF are parsed. Amino-acid changes
  are exposed under the `variant` field (1-letter, e.g. `V600E`).
- **HLA-I genotype** — the patient's class-I alleles (e.g. `HLA-A*02:01`). A neoepitope is only
  meaningful against the patient's own HLA-I, so a genotype is required; it is never invented.
- **RNA-seq expression** — a gene-level TPM table (any table with a gene identifier column and a
  TPM/expression column). All identifier columns are indexed, so genes match by symbol **or**
  Ensembl gene id. Expression is used only if supplied; it is never back-filled.
- **Optional**: a tumor BAM for RNA VAF at the variant site; NetMHCstabpan output for real
  binding stability.

### Genome build

The VCF parser reads the assembly from the `##reference` header (`hg38`/`b38`/`GRCh38` &rarr;
GRCh38; `hg19`/`b37`/`GRCh37` &rarr; GRCh37) and routes **VEP annotation** to the matching Ensembl
REST server. **However, the peptide/binding core fetches protein and CDS sequences by transcript
id from the GRCh38 server only.** So for reliable neoORF/missense sequence retrieval the VCF's
transcript ids should resolve on GRCh38. A GRCh37 VCF is best **lifted to GRCh38 and re-annotated**
before running (as was done for the Pt22 demo below). *Note:* the shipped Pt22 VCF deliberately
leaves the CSQ transcript fields blank so its missense variants resolve by gene against the reviewed
human **UniProt** proteome, making the demo deterministic and offline-stable without Ensembl
transcript calls; only the neoORF/indel path (exercised by the synthetic fixture) queries Ensembl.

### The two built-in demos

- **PRIMARY — Pt22, a real melanoma patient.** Pt22 is a real **anti-PD-1 (pembrolizumab)-treated
  metastatic cutaneous melanoma patient** from **Hugo et al., *Cell* 2016** (UCLA cohort). It was
  chosen because its **DNA somatic variants (with tumor allele read counts), tumor RNA-seq, and
  per-patient HLA-I genotype are ALL publicly open for the same patient** — no dbGaP/GDC
  controlled-access requirement — and because it **exercises the full TESLA feature set**: real DNA
  **VAF**, real **cancer-cell fraction (CCF)**, and real **`TPM × VAF`** tumor abundance. **247 real
  somatic variants** are shipped (**232 missense + 9 nonsense + 6 splice; 0 indels**). REF/ALT and
  per-variant tumor read counts (→ VAF/CCF) come from the cBioPortal study **`mel_ucla_2016`**
  (sample `Pt22`); coordinates were **lifted GRCh37&rarr;GRCh38** and REF-verified against the
  GRCh38 reference (2 variants — GSTT2, NBPF14 — lost in lift/REF-verification, documented).
  Missense variants resolve by gene against the reviewed human **UniProt** proteome (the CSQ
  transcript fields are intentionally blank), so the missense demo makes **no** Ensembl transcript
  calls and is deterministic. Expression is Pt22 **tumor RNA-seq** from **GEO GSE78220** (Hugo 2016
  FPKM matrix, sample column `Pt22.baseline`), provided as gene-level **TPM**. HLA-I is Pt22's real
  six-allele class-I genotype (A\*01:01, A\*02:01, B\*27:05, B\*37:01, C\*02:02, C\*06:02). Result
  (6 alleles): **214 of 232 missense pass WT-residue validation** (18 skipped, below) → **8009
  candidate peptides** with **VAF/CCF non-null 8009/8009 (100%)**; **Tier 1 = 67, Tier 2 = 276,
  Tier 3 = 695**, plus 1978 excluded-low-abundance and 4993 excluded-non-binder. Top Tier-1:
  **PDGFRA R376Q** `IRYQSKLKL` on HLA-C\*06:02 (%rank 0.012). The **BRAF V600E** driver is recovered
  as `IGDFGLATEK` on HLA-C\*06:02 (Tier 2, %rank 1.259).
  - *Caveats (documented, not hidden).* (1) WES/RNA-seq are from a single pre-treatment biopsy; **no
    tumor–normal pair** is shipped (the VCF `NORMAL` column is a 0/0 placeholder so the parser reads
    the `TUMOR` genotype/AF), and putative germline is removed by the gnomAD population-AF filter
    rather than tumor-vs-normal subtraction. (2) **Missense-only in practice** — Hugo 2016's public
    MAF has 0 indels across the whole cohort, so the **neoORF/frameshift path is not exercised by
    Pt22**; it is covered by the labelled synthetic fixture (below). (3) **18 of 232 missense are
    dropped** because the stated WT residue disagrees with the canonical UniProt protein
    (transcript/isoform differences in the cBioPortal RefSeq annotation) — an honest consequence of
    the no-fabrication contract, never a forced residue.
- **Synthetic code-path fixture (`demo_somatic.vcf`).** A tiny **synthetic** VCF (not a sequenced
  patient) kept only to exercise paths the real SNV-only Pt22 case cannot: **neoORF generation from
  frameshift indels** (FBXW7 p.Lys465fs, BRCA1 p.Glu826fs) and the **germline-AF / synonymous
  exclusion filters** (BRAF V600E, KRAS G12D, TP53 R248L missense, a synonymous KRAS record, and a
  common-AF BRAF record) × 6 HLA-I alleles × **GTEx v8** Skin-Sun-Exposed median TPM. GTEx measures
  **normal tissue**, so this fixture is for logic coverage only — **not** a real neoantigen example.

---

## 3. Neo-peptide generation

**Missense variants.** Handled by the reused, audited core (`generate_peptides`). The real
canonical protein sequence is fetched (UniProt accession → UniProt FASTA; Ensembl id → Ensembl;
or gene symbol → reviewed human UniProt) over **verified TLS**. The stated **wild-type residue is
validated** at the stated position before the substitution is applied; mismatches are discarded
(never forced). All overlapping **8–11mers spanning the substituted residue** are emitted, each
paired with the matched wild-type peptide and the **0-based** in-peptide mutation index `mi`.

**Indels / frameshifts → neoORF peptides** (`peptides_indel.py`). The affected transcript coding
sequence (CDS) is fetched from Ensembl; the HGVSc edit is applied; the edited CDS is translated
to the next stop codon. The **neojunction** (first residue that diverges from the wild-type
translation) is located, and every 8–11mer overlapping **≥ 1 novel residue** is emitted. For a
frameshift this yields the full novel out-of-frame peptide space up to the new stop — a rich,
tumor-specific neoantigen source that missense-only pipelines miss. The in-peptide index `mi` is
**0-based**, matching the SNV convention, so the orchestrator applies a uniform `mi + 1` to get a
1-based mutation position for the mutation-position feature.

---

## 4. Peptide–MHC-I binding (MHCflurry, mandatory)

Each candidate peptide is scored against **every** patient HLA-I allele with **MHCflurry 2.x**
(`Class1PresentationPredictor`), which returns a **presentation percentile rank** (processing +
affinity) and a predicted affinity. The **best (lowest) presentation %rank across alleles** is
retained as the peptide's binding call, along with the winning allele and affinity. Binding is
classified as **strong** (%rank < 0.5), **binder** (< 2.0), **weak** (< 10), or **non** (≥ 10).

MHCflurry is **Apache-2.0 (commercial-clean)** and **required**. There is **no synthetic or
heuristic fallback** — if MHCflurry is unavailable, or an allele is unsupported, the peptide is
**dropped**, never assigned a fabricated score. NetMHCpan is only an optional alternative and is
academic-license-only.

---

## 5. The seven TESLA features (as implemented)

Features are organised on the two TESLA axes and computed in `tesla_features.py`. Each returns a
0–1 sub-score, or `None` when the underlying real measurement is unavailable (never imputed).

### Presentation axis — does the peptide reach the cell surface on MHC-I?

1. **Binding affinity score** — from the MHCflurry %rank `r`:
   `score = (log10(10) − log10(r)) / (log10(10) − log10(1e-3))`, `r = max(r, 1e-3)`, clamped to
   [0, 1]. Lower %rank → higher score.
2. **Tumor abundance** — `abundance = TPM × VAF` (or `TPM × 1` if VAF is unknown);
   `score = log10(abundance + 1) / 3` clamped to [0, 1]. Filters: `pass_expr = TPM ≥ 5.0`,
   `pass_vaf = VAF ≥ 0.05`. **If expression is not provided, abundance is `None` (not fabricated)**
   and does not contribute.
3. **Binding stability** — real **NetMHCstabpan** half-life if supplied (`min(t½/4 h, 1)`);
   otherwise the **MHCflurry presentation score** as a documented proxy; otherwise `None`.

### Recognition axis — can a T cell see the peptide as foreign?

4. **Differential agretopicity** (`agretopicity_score`) — from agretopicity = WT %rank ÷ mut
   %rank. A value ≥ 1 means the mutation creates or improves binding relative to wild-type — the
   hallmark of a mutation-created, non-self epitope. Mapped by a log-scaled, saturating transform,
   `score = clamp(0.5 + 0.5·log2(a)/2, 0, 1)`: agretopicity 0.5 → 0.25, 1 → 0.5, 2 → 0.75, ≥ 4 →
   1.0. Requires a 1:1 wild-type peptide, so it is `None` for frameshift neoORFs (which have no
   wild-type counterpart) and does not contribute for them.
5. **Foreignness / dissimilarity-to-self** (`foreignness_score`) — a real local Smith-Waterman
   alignment (Biopython `PairwiseAligner`, BLOSUM62, local mode, gap open −11 / extend −1) of the
   peptide against two **real bundled reference sets**: a curated **IEDB immunogenic-9mer** set
   (303 human MHC-I positive 9mers) and a **human self-proteome** sample (3934 9mers tiled from 12
   abundant/housekeeping UniProt proteins). Each raw score is normalised by the peptide's
   self-alignment score. **Foreignness** = similarity to the IEDB immunogenic set (Łuksza et al.
   2017, recognition potential); **dissimilarity-to-self** = 1 − similarity to the self set
   (Richman et al. 2019). The feature score is the mean of the available components. If Biopython
   or both reference sets are missing, the feature is `None` (real-data-only) and does not
   contribute. (The CSV also reports `foreignness` and `dissim_to_self = 1 − self_similarity`.)
6. **Fraction hydrophobic** — fraction of residues with Kyte–Doolittle hydropathy > 0
   (A, C, I, L, M, F, V), a proxy for hydrophobic TCR-contact content.
7. **Mutation position** — anchor positions (P2 and the C-terminus) score low (0.2); non-anchor,
   central positions score high (0.5–1.0). A fully novel neoORF peptide with no single defined
   mutated residue leaves this `None`.

**Composite priority score** (`FEATURE_WEIGHTS`, `tesla_features.py`). A weighted sum over the
features that are **actually available**:

| Axis | Feature | Weight |
|------|---------|-------:|
| Presentation | binding affinity | 0.30 |
| Presentation | tumor abundance | 0.22 |
| Presentation | binding stability | 0.08 |
| Recognition | differential agretopicity | 0.15 |
| Recognition | foreignness / dissimilarity-to-self | 0.13 |
| Recognition | fraction hydrophobic | 0.06 |
| Recognition | mutation position | 0.06 |

The weights of any missing feature are removed and the remainder **renormalised**, so a missing
measurement neither helps nor hurts beyond removing its evidence. The result is scaled to 0–100.
The weights are **documented, not fit to outcome data**. Relative to a presentation-only
prioritiser this raises the recognition axis to ≈ 0.40 of the total, which on real neoantigens
correctly elevates a driver mutation whose mutant peptide binds far better than its wild-type
counterpart (high agretopicity) above frameshift peptides that lack a wild-type reference — the
profile most consistent with a genuine, T-cell-visible non-self epitope.

---

## 6. Tiering (the actionable call)

- **Tier 1** — strong binder (%rank < 0.5) **and** expressed above the abundance floor (or, if
  expression is unknown, VAF above floor) **and** not an anchor-only mutation. High-confidence.
- **Tier 2** — a binder (%rank < 2.0). Candidate.
- **Tier 3** — still binds (weak, %rank < 10). Reported but de-prioritized.
- **Excluded — non-binder** (%rank ≥ 10), or **excluded — low abundance** (fails the
  expression/VAF filter when that data is present).

Within a tier, peptides are ordered by composite priority score (descending).

---

## 7. Benchmark against real TESLA data

The scoring model is evaluated on the **real TESLA neoepitope dataset** (redistributed as
Mendeley Data, [doi:10.17632/6x87nx8jtc.1](https://doi.org/10.17632/6x87nx8jtc.1), CC BY 4.0):
**714 peptides**, each with its restricting
HLA-I allele and an **experimentally determined T-cell-recognition label** (33 immunogenic / 681
non-immunogenic; base rate ≈ 4.6%). Every peptide is re-scored with MHCflurry against **its own**
allele and pushed through the identical TESLA feature + ranking pipeline.

Because this public table carries peptide + allele + label but **no per-peptide expression, VAF,
or wild-type %rank**, the **tumor-abundance, mutation-position, and differential-agretopicity
features are left `None` for the benchmark** (not fabricated). The recognition axis therefore
cannot fire meaningfully here (agretopicity is undefined without wild-type %ranks, and foreignness
is not a validatable signal in a non-neoantigen context). We consequently report **two AUROCs**:

- a **presentation sub-score** (binding affinity + stability + hydrophobicity + position,
  renormalised over the available presentation-side features) — the **fair, binding-dominated
  comparator** on this table; and
- the **full composite**, which is *diluted* here because the recognition features it carries are
  inert on a non-neoantigen table.

Reported metrics (AUROC, average precision, top-K recall, enrichment) are cross-checked against
scikit-learn.

**Result on this set:** the presentation sub-score ranks immunogenic vs non-immunogenic peptides
with **AUROC ≈ 0.78**, and the full composite with **AUROC ≈ 0.77**; average precision ≈ 0.17
(≈ 3.6× the base rate), and top-20 enrichment ≈ 7.6×. Per-feature AUROCs confirm the picture:
binding affinity ≈ 0.79 and binding stability ≈ 0.79 carry the signal, while foreignness ≈ 0.46
(near-random) and fraction-hydrophobic ≈ 0.36 do not discriminate *on this benchmark* —
recognition features are the practical differentiator on **real neoantigens with wild-type
context**, not on this public table. The dominant signal is MHC-I binding (immunogenic peptides
have markedly lower %rank), reproducing the central TESLA finding.

**Interpretation caveat.** The 98%-filtered / precision-0.70 headline from TESLA reflects the
**full** feature set including tumor abundance on complete tumor data; because this public
benchmark lacks expression/VAF/wild-type %ranks, absolute *filtering* fractions computed here
understate what the full pipeline achieves when those measurements are available. The presentation
sub-score AUROC is the fair summary of what the table can test.

---

## 8. Software packages used

All commercial-clean / open-source; no academic-only or fabrication dependency:

- **MHCflurry 2.x** (Apache-2.0) — the peptide–MHC-I presentation predictor. **Required**; there
  is no synthetic fallback. NetMHCpan is only an optional alternative and is academic-license-only.
- **Biopython** (`Bio.Align.PairwiseAligner`, BSD) — real local Smith-Waterman alignment
  (BLOSUM62) for the foreignness / dissimilarity-to-self feature. No external BLAST binary is
  required; if Biopython is absent the feature stays `None` and the composite renormalises.
- **NetMHCstabpan** (optional, academic license) — real binding half-life for the stability
  feature. When not supplied, an MHCflurry presentation-based proxy is used and labelled as such.
- **numpy / pandas / scikit-learn** — feature assembly and benchmark metric computation
  (AUROC, average precision) cross-checked against scikit-learn.
- **matplotlib / reportlab / pysam** — figures, the PDF report, and VCF/BAM I/O.

## 9. Limitations

- **(i) Benchmark cannot exercise the strongest features.** The public TESLA table lacks
  expression/VAF and wild-type %ranks, so tumor abundance and differential agretopicity — among
  TESLA's most powerful signals — cannot contribute to the benchmark metrics. The presentation
  sub-score is therefore the fair comparator, and the absolute filtering fractions here understate
  what the full pipeline achieves when those measurements are available.
- **(ii) Binding stability is a proxy by default.** It uses an MHCflurry presentation-based proxy
  unless real NetMHCstabpan output is supplied.
- **(iii) Foreignness/dissimilarity uses finite reference sets.** It is computed by local
  alignment against real but finite bundled sets (a curated IEDB immunogenic-9mer set and a
  human-self-proteome sample); it is a transparent, documented signal, not a trained recognition
  model, and it did not discriminate on the non-neoantigen public benchmark (AUROC ≈ 0.46 there).
- **(iv) Predictions, not assays.** Scores are **peptide–MHC binding predictions**, not T-cell
  assays; candidates are **hypotheses for experimental validation** (e.g. multimer staining,
  ELISpot), and results depend on the accuracy of upstream **variant calling, HLA typing, and
  expression quantification**. The composite weights are documented and transparent but **not fit
  to outcome data**; treat the priority score as a ranking aid, not a calibrated probability.
- **(v) Backend reproducibility.** On some hosts the MHCflurry PyTorch backend (NNPACK
  unsupported) is not bit-reproducible across process starts; benchmark AUROC varies by roughly
  ±0.02 and tier boundaries can shift by a few peptides run-to-run, though the top-ranked
  candidates are stable. This is a host/backend property, not fabrication.

---

## References

1. Wells DK, van Buuren MM, Dang KK, et al. **Key Parameters of Tumor Epitope Immunogenicity
   Revealed Through a Consortium Approach Improve Neoantigen Prediction.** *Cell.* 2020.
   [doi:10.1016/j.cell.2020.09.015](https://doi.org/10.1016/j.cell.2020.09.015)
2. O'Donnell TJ, Rubinsteyn A, Laserson U. **MHCflurry 2.0: Improved Pan-Allele Prediction of
   MHC Class I-Presented Peptides by Incorporating Antigen Processing.** *Cell Systems.* 2020.
   [doi:10.1016/j.cels.2020.06.010](https://doi.org/10.1016/j.cels.2020.06.010) ·
   [github.com/openvax/mhcflurry](https://github.com/openvax/mhcflurry)
3. Łuksza M, Riaz N, Makarov V, et al. **A neoantigen fitness model predicts tumour response to
   checkpoint blockade immunotherapy.** *Nature.* 2017.
   [doi:10.1038/nature24473](https://doi.org/10.1038/nature24473) — foreignness / recognition
   potential.
4. Richman LP, Vonderheide RH, Rech AJ. **Neoantigen Dissimilarity to the Self-Proteome Predicts
   Immunogenicity and Response to Immune Checkpoint Blockade.** *Cell Systems.* 2019.
   [doi:10.1016/j.cels.2019.08.009](https://doi.org/10.1016/j.cels.2019.08.009) —
   dissimilarity-to-self.
5. Vita R, Mahajan S, Overton JA, et al. **The Immune Epitope Database (IEDB): 2018 update.**
   *Nucleic Acids Res.* 2019;47(D1):D339–D343.
   [doi:10.1093/nar/gky1006](https://doi.org/10.1093/nar/gky1006) — source of the bundled
   immunogenic reference peptides.
6. TESLA neoepitope benchmark dataset. **Mendeley Data**, CC BY 4.0.
   [doi:10.17632/6x87nx8jtc.1](https://doi.org/10.17632/6x87nx8jtc.1)
7. Hugo W, Zaretsky JM, Sun L, et al. **Genomic and Transcriptomic Features of Response to
   Anti-PD-1 Therapy in Metastatic Melanoma.** *Cell.* 2016;165(1):35–44.
   [doi:10.1016/j.cell.2016.02.065](https://doi.org/10.1016/j.cell.2016.02.065) — source of the
   Pt22 somatic variants, tumor RNA-seq (GEO **GSE78220**), and HLA-I for the primary demo.
8. Cerami E, et al. **The cBio Cancer Genomics Portal.** *Cancer Discov.* 2012;2(5):401–404.
   [doi:10.1158/2159-8290.CD-12-0095](https://doi.org/10.1158/2159-8290.CD-12-0095); Gao J, et al.
   *Sci Signal.* 2013;6(269):pl1.
   [doi:10.1126/scisignal.2004088](https://doi.org/10.1126/scisignal.2004088) — cBioPortal
   (study `mel_ucla_2016`) used to fetch the Pt22 variants, tumor read counts, and HLA-I.
9. The UniProt Consortium. **UniProt: the Universal Protein Knowledgebase.** *Nucleic Acids Res.*
   — reviewed human proteome; source of the canonical missense protein sequences (the Pt22 demo
   resolves missense by gene against UniProt). [uniprot.org](https://www.uniprot.org)
10. GTEx Consortium. **The GTEx v8 resource** (synthetic code-path fixture expression = Skin – Sun
    Exposed median TPM). [gtexportal.org](https://gtexportal.org)
11. Ensembl (transcript CDS / REST API for indel neoORFs; GRCh37&rarr;GRCh38 liftover for the
    demo variants). [rest.ensembl.org](https://rest.ensembl.org)

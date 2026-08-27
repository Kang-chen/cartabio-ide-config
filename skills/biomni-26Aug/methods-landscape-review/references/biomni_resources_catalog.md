# Biomni — Supported Resources (Phylo A2 platform)

> **How this skill uses this file (Step 7).** This is a static seed snapshot. At
> runtime, verify packages with direct imports, HPC tools with `hpc_search_tools`,
> and sibling skills with `Skill`; verified runtime results take precedence. Use
> this file to know what categories exist and produce a useful
> "Relevant Biomni resources" section. The report's resource section should name
> concrete databases/datasets/packages and
> **cross-reference the sibling skill(s) that actually run the recommended method**.
>
> **Task-type → resource mapping (starting points; verify directly at runtime):**
> - *Bulk RNA-seq DE / method comparison*: R pkgs `DESeq2`, `apeglm`, `edgeR`(via limma
>   stack), `limma`, `tximport`; data `GTEx`, `MSigDB`, `RummaGEO`; skills
>   `bulk-rnaseq-differential-expression`, `bulk-rnaseq-counts-to-de-deseq2`,
>   `functional-enrichment-from-degs`, `pathway-enrichment`.
> - *Alignment / quantification*: HPC tools `star`, `hisat2`, `salmon`, `kallisto`,
>   `minimap2`, `bowtie2`; CLI `samtools`, `bwa`, `bedtools`.
> - *Variant calling / annotation*: HPC `clair3`, `deepvariant`(pepper), `strelka2`,
>   `freebayes`, `bcftools`; DBs `gnomAD`, `NCBI`, `Ensembl`, `UCSC`; skill
>   `genetic-variant-annotation`.
> - *scRNA-seq (integration, trajectory, ambient RNA, segmentation)*: pkgs `scanpy`,
>   `scvi-tools`, `Seurat`, `harmony-pytorch`, `scvelo`, `scrublet`; HPC `cellbender`,
>   `cellpose`, `regvelo`; skills `scrnaseq-scanpy-core-analysis`,
>   `scrnaseq-seurat-core-analysis`, `scrna-trajectory-inference`.
> - *Enrichment / pathways / regulators*: pkgs `gseapy`, `clusterProfiler`, `enrichplot`,
>   `msigdbr`; DBs `KEGG [NC — excluded]`, `Reactome`, `MSigDB`, `Gene Ontology`; skills
>   `pathway-enrichment`, `functional-enrichment-from-degs`, `upstream-regulator-analysis`.
>   (For commercial-safe pathway work recommend `Reactome`/`Gene Ontology`/`MSigDB`, not KEGG.)
> - *Structure prediction / protein design*: HPC `alphafold-v2`, `boltz-2`, `chai-1`,
>   `esmcfold2`, `rfdiffusion`, `proteinmpnn`, `thermompnn`; DBs `RCSB PDB`, `AlphaFold DB`,
>   `UniProt`, `InterPro`.
> - *Drug / target / pharmacology*: DBs `ChEMBL [SA]`, `OpenFDA`, `cBioPortal`, `STRING`; data
>   `DepMap`, `LINCS L1000`, `Broad Drug Repurposing Hub`, `TxGNN`; pkgs `rdkit`,
>   `deeppurpose`; tool `predict_admet_properties`. (Recommend `ChEMBL` only with a
>   ShareAlike/attribution caveat; `OpenFDA`/`cBioPortal` are permissive alternatives.)
> - *Survival / clinical / ML biomarker*: pkgs `lifelines`, `scikit-learn`, `statsmodels`;
>   skills `survival-analysis-clinical`, `elastic-net-biomarker-panel`,
>   `experimental-design-statistics`.
> - *Clinical-trial / literature landscape*: skills `clinicaltrials-landscape`,
>   `literature-review`; DB `ClinicalTrials.gov`.
>
> The full inventory follows.

================================================================
## 0. LICENSING & COMMERCIAL-USE POLICY (read before recommending anything)
================================================================

This skill produces decision-support reports that may inform **commercial** work. Some Biomni
resources carry licenses that forbid or restrict commercial use. **Recommendations must respect
these licenses.** Apply the following policy whenever building the "Relevant Biomni resources"
section (Step 7), including to resources found during runtime verification.

**Tag legend**
- `[NC]` — **Non-commercial license. DO NOT recommend.** Omit the resource from recommendations
  and add a one-line note that it was excluded for commercial-licensing reasons. Do not curate a
  replacement (simply omit + note).
- `[SA]` — **ShareAlike / attribution license. Recommend only with an explicit caveat** that the
  resource requires attribution and/or share-alike redistribution terms for downstream/commercial use.
- *(untagged)* — treated as **permissive / commercial-safe**; recommend normally.

**Restricted-resource table** (the only resources that carry constraints; everything else in this
catalog is treated as permissive/clean):

| Resource | Class | Action in recommendations |
|---|---|---|
| KEGG | `[NC]` | Omit + note. For pathways use permissive options already in this catalog (Reactome, Gene Ontology, MSigDB). |
| DisGeNET | `[NC]` | Omit + note (gene–disease). |
| OMIM | `[NC]` | Omit + note. *Not in current inventory; listed so the guardrail is complete.* |
| DrugBank | `[NC]` | Omit + note. *Not in current inventory; listed so the guardrail is complete.* |
| COSMIC | `[NC]` | Omit + note. *Not in current inventory; listed so the guardrail is complete.* |
| ChEMBL | `[SA]` | May recommend **with** a ShareAlike/attribution caveat. |
| Human Protein Atlas (HPA) | `[SA]` | May recommend **with** a ShareAlike/attribution caveat. |
| PharmGKB (ClinPGx) | `[SA]` | May recommend **with** a ShareAlike/attribution caveat. |

Inline `[NC]`/`[SA]` tags appear next to these resources throughout the inventory below; untagged
resources are permissive.

The full inventory follows.

Complete catalog of tools, databases, and packages available to the Biomni-powered A2 agent.
Source: `phylo-dev/phylo-monolith` — `backend/app/agents/a2/` (tool schemas, `env_desc.py`) and `hpc/api/tools_registry/catalogue/`.

Summary
- 52 HPC (GPU/CPU cluster) tools — incl. AlphaFold, Boltz, Chai, ESM, RFdiffusion, ProteinMPNN, ThermoMPNN
- 30 molecular-biology + pharmacology agent tools
- 1 integration (Addgene) + Nextflow pipeline runner
- 17 queryable external databases
- 132 curated data-lake datasets across 23 sources
- 86 preinstalled software packages (54 Python + 19 R + 13 CLI)

================================================================
## 1. HPC TOOLS (52) — run on GPU/CPU clusters via `hpc_run_tool`
================================================================

### Protein / biomolecular structure prediction
- alphafold-v2 — AlphaFold v2. 3D protein structures from sequence (monomers, homomers, heteromers).
- boltz-2 — Boltz-2. Structures of proteins, nucleic acids, small molecules & complexes.
- chai-1 — Chai-1. Multimodal structures: proteins, small molecules, DNA, RNA, glycosylation, assemblies.
- esmcfold2 — ESMCFold2. Full protein structure prediction on one GPU.
- esmcfold2-fast — ESMCFold2-Fast. Fast protein structure prediction.
- immunebuilder — ImmuneBuilder. Antibody, nanobody & TCR structure prediction.

### Protein design & engineering
- boltzgen — BoltzGen. Universal binder design vs proteins, peptides, small molecules, DNA, RNA.
- proteinmpnn — ProteinMPNN. Sequence design for a given backbone.
- rfdiffusion — RFdiffusion. Backbone generation (scaffolding, binder design, symmetric oligomers).
- rfantibody — RFAntibody. Structure-based de novo antibody/nanobody design.
- thermompnn — ThermoMPNN. Predicts stability change (ddG) for point mutants.

### Protein language models / embeddings & structure search
- esmc — ESMC-6B protein LM embeddings, masked-LM logits, mutation scores.
- foldseek — Ultra-fast protein structure search & clustering (vs AlphaFold DB, etc.).

### Sequence search & clustering
- diamond — Ultra-fast protein / translated-DNA alignment (100–10,000x BLAST).
- mmseqs2 — Ultra-fast protein & nucleotide search / clustering / taxonomy.

### Read alignment / RNA-seq quantification
- bowtie2 — Gapped short-read aligner.
- hisat2 — Splice-aware graph FM aligner (DNA/RNA).
- minimap2 — Long & short read pairwise aligner.
- star — Splice-aware RNA-seq aligner (+ STARsolo single-cell).
- salmon — Transcript quantification (quasi-mapping).
- kallisto — Pseudoalignment transcript quantification.

### Genome assembly
- canu — Long-read OLC assembler (PacBio/ONT; EOL).
- flye — Long-read repeat-graph assembler.
- hifiasm — Haplotype-resolved HiFi assembler (+Hi-C/trio/ONT).
- megahit — Fast metagenome assembler.
- nextdenovo — Correct-then-assemble long-read assembler.
- raven — Fast long-read assembler.
- spades — Illumina/IonTorrent + hybrid assembler (isolate/meta/plasmid/SC/RNA).
- trinity — De novo RNA-seq transcriptome assembler.
- unicycler — Hybrid bacterial isolate assembler.
- verkko — Telomere-to-telomere hybrid assembler.
- wtdbg2 — Fast fuzzy-Bruijn long-read assembler.

### Assembly / QC reporting
- quast — Assembly quality metrics.
- checkm2 — ML MAG completeness/contamination assessment.
- multiqc — Aggregate QC reports across 100+ tools.

### Variant calling
- bcftools — VCF/BCF variant calling & manipulation.
- clair3 — Deep-learning germline caller (ONT/HiFi/Illumina).
- freebayes — Bayesian haplotype variant detector.
- longshot — Diploid SNV caller + phasing (long reads).
- nanocaller — CNN long-read variant caller.
- pepper-deepvariant — Haplotype-aware long-read caller pipeline.
- sniffles — Structural variant caller (long reads).
- strelka2 — Germline & somatic small-variant caller (Illumina, tumor/normal).

### Genome / RNA annotation & fusion
- bakta — Bacterial/plasmid genome annotation (modern Prokka).
- prokka — Prokaryotic genome annotation.
- stringtie — Transcript assembly & quantification.
- star-fusion — Fusion-gene detection from RNA-seq.

### Single-cell / imaging
- cellbender — Removes ambient RNA from droplet scRNA-seq.
- cellpose — Deep-learning cell/nucleus segmentation.
- regvelo — RNA-velocity / regulatory dynamics.

### Utility
- dd — Data duplicator (file copy/size utility).
- gpu-test — GPU availability check (nvidia-smi).

Also: `hpc_run_pipeline` runs arbitrary Nextflow (nf-core) pipelines on the cluster.

================================================================
## 2. MOLECULAR BIOLOGY TOOLS (29)
================================================================
find_open_reading_frames, compare_sequences_for_mutations, fetch_gene_coding_sequence,
align_primers_to_sequence, design_simple_primer, design_pcr_primers_with_overhangs,
design_sanger_verification_primers, run_pcr_reaction, run_multi_primer_pcr,
find_specific_restriction_sites, find_all_common_restriction_sites, digest_with_restriction_enzymes,
design_golden_gate_insert_oligos, get_oligo_annealing_protocol, get_golden_gate_protocol,
perform_golden_gate_assembly, design_complete_gibson_assembly, perform_gateway_lr_reaction,
get_gateway_lr_protocol, compare_knockout_cas_systems, compare_delivery_methods,
design_crispr_knockout_guides, assemble_overlapping_oligos, get_transformation_protocol,
get_transfection_protocol, get_lentivirus_production_protocol, get_facs_sorting_protocol,
get_gene_editing_amplicon_pcr_protocol, get_western_blot_protocol

## PHARMACOLOGY TOOLS (1)
predict_admet_properties (ADMET prediction from SMILES)

================================================================
## 3. INTEGRATIONS
================================================================
- Addgene: search_plasmids, get_plasmid, get_plasmid_with_sequences, get_addgene_sequence_files

================================================================
## 4. QUERYABLE DATABASES (17, with bundled query schemas)
================================================================
AlphaFold DB, BioGRID, cBioPortal, ChEMBL [SA], ClinicalTrials.gov, Ensembl, gnomAD,
InterPro, KEGG [NC], Monarch, NCBI, OpenFDA, RCSB PDB, Reactome, STRING, UCSC, UniProt
(License tags: KEGG [NC] = do not recommend; ChEMBL [SA] = recommend only with a
ShareAlike/attribution caveat. All others in this list are permissive.)

================================================================
## 5. DATA LAKE — 132 curated datasets across 23 sources
================================================================
- GTEx (25) — tissue gene expression (v11), eQTLs, outlier calls
- DepMap (19) — CRISPR gene effect/dependency, expression, model metadata
- ClinPGx / PharmGKB (18) [SA] — clinical pharmacogenomics annotations & variants (recommend only with a ShareAlike/attribution caveat)
- ENCODE SCREEN cCRE (12) — candidate cis-regulatory elements
- LINCS L1000 (10) — perturbation transcriptomic signatures/gene sets
- MSigDB (10) — human gene sets (H + C1–C8)
- RummaGEO (8) — GEO-derived gene-expression signatures
- MouseMine (6) — mouse gene sets (MH/M1–M8)
- GeneBass (5) — pLoF / missense / synonymous variants
- CellMarker2 (3) — single-cell cell-type markers
- Broad Drug Repurposing Hub (2) — molecules + MoA/target info
- Human Protein Atlas (2) [SA] — protein expression (recommend only with a ShareAlike/attribution caveat)
- TxGNN (2) — drug-repurposing predictions & name mapping
- Single-file databases (1 each): DisGeNET [NC] (do not recommend; omit + note), McPAS-TCR (TCR), P-HIPSTER (virus-host PPI),
  Gene Ontology (go-plus), GWAS Catalog, Human Phenotype Ontology (hp.obo), BioGRID,
  PrimeKG (precision-medicine knowledge graph), Addgene, Enamine REAL library

================================================================
## 6. PREINSTALLED PACKAGES (86)
================================================================

### Python (54)
biopython, biom-format, scanpy, scikit-bio, anndata, mudata, pyliftover, biopandas, biotite,
gget, lifelines, scvi-tools, gseapy, scrublet, cellxgene-census, GEOparse, scvelo, pysam,
pyfaidx, pyranges, pybedtools, rdkit, deeppurpose, pyscreener, descriptastorus, pandas, numpy,
scipy, scikit-learn, matplotlib, seaborn, plotnine, statsmodels, pymc3, umap-learn, faiss-cpu,
harmony-pytorch, tiledbsoma, h5py, tqdm, joblib, opencv-python, pypdf, scikit-image, igraph,
pyscenic, PyMassSpec, python-libsbml, cobra, reportlab, fcsparser, hmmlearn, tskit, cyvcf2

### R (19)
ggplot2, ggprism, ggrepel, ComplexHeatmap, dplyr, tidyr, DESeq2, apeglm, tximport,
clusterProfiler, enrichplot, msigdbr, AnnotationDbi, org.Hs.eg.db, org.Mm.eg.db, Seurat,
RColorBrewer, tibble, readr

### CLI tools (13)
samtools, bowtie2, bwa, bedtools, fastqc, trimmomatic, gatk, mafft, plink, plink2, vina,
autosite, nextflow

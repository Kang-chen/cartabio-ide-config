---
id: "skill_1a2d4c938cf0cd29657bf065517bca3a"
name: "cell-therapy-car-design"
description: "Use for end-to-end scFv-based CAR design coupled to reanalysis of pooled loss-of-function CRISPR-KO proliferation or fitness screens in T cells. Covers MAGeCK hit nomination, DepMap checks, second-generation CAR architecture, codon-optimized ORFs, and lentiviral GenBank cassettes from a validated scFv."
category: "functional_genomics"
visibility: "public"
starting-prompt: "Reanalyze my pooled CRISPR-KO T-cell screen to nominate CAR-T targets, then design a second-generation CAR construct from my scFv."
---

# CAR-T Target Discovery + Receptor Design (Biomni-native)

Two linked capabilities that combine into a next-generation CAR-T design — a
receptor **plus** a prioritized, essentiality-filtered list of knockout targets:

1. **CRISPR screen reanalysis** — reanalyze a pooled loss-of-function screen in
   primary human T cells (SLICE-style) with the authentic MAGeCK workflow, then
   **validate hits against DepMap** to separate context-specific regulators from
   broadly essential genes.
2. **CAR design** — build clinically-validated second-generation CAR constructs
   from a validated scFv as an annotated protein map, a human codon-optimized ORF,
   and a complete lentiviral expression cassette with GenBank annotation.

Worked example throughout: **FMC63 anti-CD19** CAR + the **Shifrut 2018 SLICE**
screen (GEO GSE119450). The workflow generalizes to **any scFv-based CAR** and
**any pooled CRISPR-knockout proliferation/fitness screen** — swap the antigen/scFv
and the dataset accession.

## Native-first principle (read this first)

Prefer **Biomni-native tools, databases, and data-lake datasets** for every step;
fall back to external CLIs/REST only where there is genuinely no native equivalent
(this is called out at each such step). Two consequences:

- **Verify software directly before use:** import the documented `biomni.tool`
  functions and check packages/CLIs with imports or `command -v`.
- **Do not promise tools that are not installed.** In this environment,
  `design_crispr_knockout_guides` appears in some inventories but is **NOT** in the
  installed `biomni.tool` package — there is no native sgRNA guide-design tool.
  Source guides from the published library / an Addgene KO library / a validated
  published set, and use `compare_knockout_cas_systems()` to guide KO strategy.

### Biomni resources this skill uses
- **`LiteratureSearch`** — confirm the screen's experimental design, ground the
  report Introduction/Background, and corroborate each top hit's known T-cell role.
  Use this for *all* literature actions (never assert citations from memory).
- **Direct environment checks** — import documented functions and check packages/CLIs.
- **DepMap** — broad-essentiality cross-check for screen hits.
- **`biomni.tool.integrations`** — `search_plasmids`, `get_plasmid_with_sequences`,
  `get_addgene_sequence_files` for Addgene-deposited CAR plasmids/parts.
- **`biomni.tool.molecular_biology`** — `fetch_gene_coding_sequence` (NCBI CDS),
  `compare_knockout_cas_systems`, `get_lentivirus_production_protocol`,
  `get_facs_sorting_protocol` for design parts and wet-lab handoff.
- **Packages** — `GEOparse` (GEO metadata/SRA linkage), `pandas`/`numpy`,
  `Biopython` (GenBank), `mageck` CLI (screen stats), `reportlab`+`pypdf` (report).
  `gseapy` is optional (`uv pip install gseapy`) for pathway corroboration.
- **`pdf-report-generation` skill** — load it for the final PDF (styling + build).
- External fallback (no native equivalent): **sra-tools** for FASTQ download.

## When to use this skill

- "Design a CD19 (or other target) CAR construct" — especially when both a 4-1BB
  and a CD28 version are wanted, or GenBank/codon-optimized DNA is required.
- "Reanalyze / run MAGeCK on a T-cell CRISPR screen" (Shifrut SLICE, GSE119450, or
  any CFSE/fitness proliferation screen) and nominate editing targets.
- Any task that wants a receptor **and** an essentiality-filtered menu of KO targets.
- **Especially** when the screen's guide-to-gene library table is paywalled or
  missing — the read-driven reconstruction below recovers it from the reads.

## Critical decision points (ask the user if unclear)

1. **Antigen / scFv source**: which target, and from which validated source? Always
   take the scFv from an experimentally-validated reference (Addgene deposit, a PDB
   structure, or a published clinical sequence), never a reconstruction. For FMC63,
   PDB **7URV** chain D is the validated scFv. (Native sourcing order in
   `references/car_design.md`.)
2. **Costimulatory domain**: 4-1BB (BBz, tisagenlecleucel-style, slow/persistent)
   vs. CD28 (28z, axicabtagene-style, fast/intense). Default to building BOTH.
3. **Screen contrast**: confirm from the paper via `LiteratureSearch`. For a CFSE
   proliferation screen, treatment = CFSE-low (dividing, "Div"), control = CFSE-high
   (non-dividing, "NonDiv"); guides enriched in Div → knockout ENHANCES
   proliferation (brake). Wrong direction silently inverts every hit.
4. **Normalization**: use non-targeting controls (NTCs) for MAGeCK size-factor and
   null-model estimation when present (`--control-sgrna` + `--norm-method control`);
   always add a median-norm sensitivity run.
5. **Hit-validation reference**: default = **DepMap** broad essentiality (flags genes
   that deplete in almost any cell). **Ask** whether the user prefers an alternative
   — a lineage-restricted DepMap subset, a published primary-T-cell screen, or
   pathway enrichment. Proceed with DepMap only if unspecified.
   (`references/hit_validation.md`.)

## Part 1 — CRISPR screen reanalysis + hit validation

Full detail: `references/screen_reanalysis.md`, `references/hit_validation.md`,
`references/read_driven_library_reconstruction.md`.
Helper scripts: `scripts/reconstruct_library.py`, `scripts/run_mageck.sh`,
`scripts/depmap_crosscheck.py`.

1. **Confirm design with `LiteratureSearch`** (phenotype, treatment fraction, donor
   structure, library). Cite in the report.
2. **Get data (native-first).** Resolve series/samples/SRA linkage with **GEOparse**
   (`GEOparse.get_GEO("GSE119450")`); download raw FASTQ with **sra-tools**
   (`prefetch`/`fasterq-dump`) — the one unavoidable external fallback. Map
   SRR→sample from the GEOparse metadata.
3. **Inspect read structure first** (`zcat file | head`). Pooled-screen reads are
   short (~50 bp) with a constant vector anchor flanking the 20-nt spacer — do NOT
   assume the spacer is at position 0.
4. **Reconstruct the library from reads if the guide table is missing/paywalled**
   (the novel technique — `references/read_driven_library_reconstruction.md`):
   find the anchor, extract the 20-nt spacer, reverse-complement to match the
   reference (e.g., Brunello), keep genes with enough guides observed, recover NTCs
   as top non-library spacers (U6 G-start signature).
5. **Write the MAGeCK library file**: CSV `sgRNA,sequence,gene`, **NO header**.
6. **Run MAGeCK** (`scripts/run_mageck.sh`): `mageck count` (auto-detect guide
   length for bare-spacer reads) then `mageck test` (treatment vs control, NTCs via
   `--control-sgrna`, `--norm-method control`) + a `--norm-method median`
   sensitivity run.
7. **QC**: mapping rate (~70-80% typical; Shifrut ~76%), zero-count guides (few),
   Gini index (< ~0.1 = even library; Shifrut ~0.05), NTC centering (median LFC ~0).
8. **Internal biology check**: confirm known brakes enrich (CBLB, CD5, PTEN) and
   TCR-essential genes deplete (CD3D, LCP2, ITK).
9. **Validate hits vs DepMap** (`scripts/depmap_crosscheck.py`): read ONLY the
   queried gene columns (memory-safe) from the 430 MB matrix; report per-gene mean
   gene-effect + fraction of dependent lines; flag pan-essential genes. Interpret:
   a screen hit that is pan-essential in DepMap is a weaker CAR-T target; a hit that
   is NOT pan-essential (e.g., CD3D is essential in the T-cell screen but not
   pan-essential in cancer lines) is context-specific. **Caveat: DepMap lines are
   cancer cell lines, not primary T cells** — use only to flag broad essentiality.

### Interpreting hits
- **Positive selection** (enriched in Div, `pos|*`) = knockout ENHANCES
  proliferation = a **brake**. Actionable KO candidates: CBLB, CD5, PTEN (and, in
  genome-wide screens, RASA2, SOCS1, TCEB2/ELOB).
- **Negative selection** (depleted in Div, `neg|*`) = knockout IMPAIRS
  proliferation = **essential/effector** (CD3D, LCP2, ITK) — the boundaries of safe
  editing; do not knock these out.
- `gene_summary` note: `pos|lfc == neg|lfc` = the gene-median LFC.

## Part 2 — CAR design workflow

Full detail: `references/car_design.md`. Helper: `scripts/design_car.py` (assembles
protein + codon-optimized ORF + lentiviral cassette GenBank for BBz and 28z given
an scFv).

1. **Obtain the scFv (native-first)**: search Addgene
   (`search_plasmids(genes=<antigen>)` → `get_plasmid_with_sequences` /
   `get_addgene_sequence_files`) and/or fetch the validated structure from RCSB PDB
   (FMC63 = 7URV chain D). Arrange as `VL-(G4S)3 linker-VH`.
2. **Assemble the ORF**: `CD8a signal + scFv + hinge + TM + costim + CD3z`. CD8a
   hinge/TM for BBz; CD28 hinge/TM for 28z. Canonical human domain sequences in
   `references/car_design.md`; use `fetch_gene_coding_sequence` for gene-level CDS
   when needed.
3. **Codon-optimize** the ORF for *Homo sapiens* (keeps Kozak + stop; verify the
   translation is identical to the intended protein).
4. **Build the transfer cassette**: `EFS promoter + Kozak + CAR ORF + WPRE`.
5. **Emit per construct**: protein FASTA, codon-optimized ORF FASTA, CAR-ORF
   GenBank, full-cassette GenBank, domain-boundary table, scaled architecture figure.
6. **Wet-lab handoff (native)**: `get_lentivirus_production_protocol()`,
   `get_facs_sorting_protocol()`, `compare_knockout_cas_systems()` for the
   validation plan.

## Final deliverable (required): Phylo-branded PDF with a summary infographic

Full spec: `references/reporting.md`. **Load the `pdf-report-generation` skill** and
build ONE PDF that:
- **Opens with a data-driven summary infographic** (`scripts/build_infographic.py`,
  a ReportLab `Drawing` of KPI cards + diverging hit-ranking bars). It visualizes
  only verified, computed numbers. This is a data plot — do **not** use
  `GenerateImage` for it (reserve `GenerateImage` for an optional conceptual MOA
  cartoon in the background section).
- Then includes, in order: **Introduction/Background** (cited via
  `LiteratureSearch`), **Methods** (tool-attributed), **Results** (CAR design
  figures/tables + screen QC/hits + the **DepMap cross-check table**),
  **Conclusions**, **Figures**, **References** (inline `[N]`; no hand-written
  bibliography), **Next steps** (wet-lab validation path), and **Limitations**.
- **Validate** before finishing: `pypdf` page/text check + `Read`
  `media_output_check` on the infographic and each figure page; fix blanks/clipping/
  overlaps and re-check. Save to `/mnt/results/report_<task>.pdf`.

## Honest limitations to state in any report

- A **targeted pilot** sub-library is discovery-grade, not genome-wide.
- **Few donors** (e.g., n=2) → underpowered for stringent genome-wide FDR; often
  only the single strongest hit clears FDR<0.05. Report other hits as ranked
  nominations backed by effect size + cross-donor concordance.
- A **read-driven / reference-subsetted** library is a transparent reconstruction,
  not the original supplementary table; it may differ at the margins.
- **DepMap cross-check uses cancer cell lines, not primary T cells** — it flags
  broad essentiality only; it does not confirm T-cell-specific biology.
- There is **no native guide-design tool** here; guides must come from the library/
  Addgene/a published set.
- CAR constructs assembled from canonical domains are **computational designs**
  requiring experimental validation (expression, cytotoxicity, in vivo potency).

## Deliverables checklist

- **Screen**: reconstructed sgRNA library, MAGeCK count matrix (raw + normalized),
  count summary, gene + sgRNA summaries, positive/negative hit tables, **DepMap
  cross-check table**, QC + volcano + top-hits figures.
- **Per CAR**: protein FASTA, codon-optimized ORF FASTA, CAR-ORF `.gb`, cassette
  `.gb`, domain table, architecture figure.
- **Report**: one Phylo-branded PDF opening with the summary infographic and
  containing intro, methods, results, conclusions, figures, references, next steps,
  and limitations.

## Reference files

- `references/screen_reanalysis.md` — MAGeCK commands, QC thresholds, contrast setup, native retrieval.
- `references/read_driven_library_reconstruction.md` — the paywall-circumventing technique (most novel part).
- `references/hit_validation.md` — DepMap cross-check (default) + alternatives, with caveats.
- `references/car_design.md` — native-first part sourcing, domain sequence catalog, scFv provenance, cassette layout.
- `references/reporting.md` — Phylo PDF structure + summary-infographic spec + validation.
- `scripts/reconstruct_library.py` — anchor-based spacer extraction + reference matching + NTC recovery.
- `scripts/run_mageck.sh` — count + test + sensitivity commands.
- `scripts/depmap_crosscheck.py` — memory-safe DepMap broad-essentiality cross-check for hits.
- `scripts/design_car.py` — CAR assembly + codon optimization + GenBank writer.
- `scripts/build_infographic.py` — data-driven ReportLab summary-infographic panel for the report.

# Data sources, licensing, and attribution

This skill is designed to be safe for **commercial** use. This file records the data
sources it depends on, their licenses, and the deliberate choices made to avoid
non-commercial-licensed content.

## What this skill uses

| Source | What for | License / terms | Commercial use |
|---|---|---|---|
| **EC numbers** (IUBMB Enzyme Commission nomenclature) | Feature space for functional differential abundance (Stage 3) and metabolite modules (Stage 4) | Open IUBMB nomenclature standard; the official Enzyme List is published via **ExplorEnz** | **Yes** — free, open standard |
| **PICRUSt2** (v2.5.2) | Predicts per-sample **EC** abundances by default from 16S | GPL-3.0 (software) | Yes |
| **ExplorEnz / IUBMB Enzyme Nomenclature** | Verify EC reactions/names when curating modules | Free to browse/query | Yes |
| **Rhea** (EBI) | Optional: curated reactions per EC | **CC BY 4.0** | Yes (with attribution) |
| **Reactome**, **UniProt**, **NCBI** | Optional host-side / annotation context | Reactome CC0; UniProt CC BY 4.0; NCBI public domain | Yes |
| Python/R analysis packages | Diversity, stats, plotting, PDF (see `biomni_resources.md`) | Their respective OSI licenses | Yes |

**Attribution to include in reports** when EC-based functional predictions are used:
> Functional potential was predicted from 16S rRNA data with **PICRUSt2** (Douglas et
> al., *PICRUSt2 for prediction of metagenome functions*, Nature Biotechnology, 2020),
> using its default **EC number** (IUBMB Enzyme Commission) output. Enzyme identities
> were verified against the IUBMB Enzyme List (ExplorEnz).

## What this skill intentionally does NOT use, and why

- **KEGG / KEGG Orthology (KO) / KEGG pathways & modules — REMOVED.** KEGG's own legal
  terms state that *"Non-academic use of KEGG requires a commercial license"* and that
  *"KEGG is not a public database."* Commercial use requires a paid license (via Pathway
  Solutions Inc.). Therefore **no KO identifiers and no KEGG pathway/module data ship in
  this skill**, and KO is not used by default. The metabolite modules were re-keyed from
  KO to the corresponding **EC numbers**, which are a separate, free IUBMB standard that
  KEGG cross-references but does not own.

- **MetaCyc / BioCyc — AVOIDED.** Although PICRUSt2 can infer MetaCyc pathway abundances,
  MetaCyc/BioCyc became **subscription-only on 2024-01-01**; commercial use requires a
  paid subscription and bulk data files are paywalled. This skill therefore does **not**
  ship or depend on MetaCyc pathway data. It stays at the **EC** layer (free) for
  functional interpretation. If you want higher-level pathway roll-ups, obtain your own
  MetaCyc/BioCyc subscription or use a free pathway resource you are licensed for.

- **eggNOG / GO — available but not adopted.** The eggNOG database and Gene Ontology are
  CC BY 4.0 (commercial-OK); eggNOG-mapper is AGPL-3.0 (fine to *use* — AGPL only
  constrains redistribution of a modified copy of the tool's own source). We did not
  adopt them because they would add a large (~tens of GB) dependency and change the
  pipeline inputs, which is unnecessary for these curated metabolite modules. EC (already
  PICRUSt2's default output) covers the modules cleanly. This is a viable future
  extension if broader GO-term functional profiling is needed.

## Optional academic KO mode (off by default)

`scripts/metabolite_modules.py` has `USE_KO=False` by default. If an academic user who
is covered by KEGG's academic terms wants KO-based modules, they can set `USE_KO=True`
and supply **their own** KO abundance table and KO→module map. The skill still ships
**no KEGG data**; the flag only reuses the same statistics on KO features, and it prints
a license warning when enabled. Do not enable this path for commercial work.

## License verdicts (verified against primary sources)

- KEGG: commercial use requires a license; "not a public database" (kegg.jp / genome.jp
  legal pages). → removed from the shipped default.
- EC numbers: IUBMB Enzyme Commission nomenclature; free, open; PICRUSt2's default trait.
- MetaCyc/BioCyc: subscription-only since 2024-01-01 (biocyc.org). → avoided.
- eggNOG DB: CC BY 4.0; eggNOG-mapper tool: AGPL-3.0; GO: CC BY 4.0. → allowed, not adopted.

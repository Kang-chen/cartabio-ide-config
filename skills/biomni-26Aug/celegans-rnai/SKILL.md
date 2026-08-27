---
name: celegans-rnai
version: 1.0.0
description: >
  C. elegans L4440 feeding RNAi design and evaluation.
  Two functions: (1) design — given a gene name, auto-retrieve sequence and
  output primer pairs with restriction-site adapters ready for L4440 cloning;
  (2) evaluate — given a gene name and an existing RNAi target sequence or
  primer pair, score it across four quality dimensions.
triggers:
  - C. elegans RNAi design
  - 线虫 RNAi 设计
  - worm gene knockdown
  - L4440 feeding RNAi
  - RNAi vector cloning C. elegans
  - RNAi 效果评价
  - evaluate RNAi sequence
author: Biomni / Phylo
---

# C. elegans RNAi Design & Evaluation Skill

## Overview

This skill covers two tightly related workflows for *C. elegans* feeding RNAi
using the L4440 vector system:

| Function | Trigger phrase examples | Key output |
|----------|------------------------|------------|
| **design** | "design RNAi for daf-2", "帮我设计 kynu-1 的 RNAi 引物" | Primer CSV + gene-structure SVG + chat summary |
| **evaluate** | "evaluate this RNAi sequence for daf-16", "评价这段 RNAi 序列的效果" | Evaluation report CSV + chat summary |

---

## Function 1 — `design`

### When to invoke
User provides a *C. elegans* gene name (or WormBase Gene ID) and asks to
design, create, or generate an RNAi construct / primers.

### Required inputs
- Gene name (e.g. `kynu-1`, `daf-2`) **or** WormBase Gene ID (e.g. `WBGene00002264`)

### Optional parameters (ask if not specified)
| Parameter | Default | Description |
|-----------|---------|-------------|
| `n_candidates` | 3 | Number of non-redundant target regions to return |
| `target_length` | 200 bp | Sliding-window size for target region screening |
| `gc_min` / `gc_max` | 40 / 60 % | GC content filter for candidate regions |

### Outputs
1. **`{gene}_RNAi_primers.csv`** — saved to `/mnt/results/`
2. **`{gene}_target_region.svg`** — gene structure diagram, saved to `/mnt/results/`
3. **Chat summary** — top candidate primer sequences, key QC metrics, cloning note

### Implementation
See `impl.md` → Section A (Design Module).

---

## Function 2 — `evaluate`

### When to invoke
User provides a *C. elegans* gene name **plus** an existing RNAi sequence
(target region DNA or primer pair) and asks to assess, score, or evaluate it.

### Required inputs
- Gene name or WormBase Gene ID
- RNAi sequence in one of two formats (auto-detected):
  - **Target region sequence**: ~200 bp DNA string (no RE adapters)
  - **Primer pair**: Forward + Reverse primer sequences (RE adapters stripped automatically)

### Outputs
1. **`{gene}_RNAi_evaluation.csv`** — detailed scores, saved to `/mnt/results/`
2. **Chat evaluation report** — Markdown table with four dimension scores + overall recommendation

### Implementation
See `impl.md` → Section B (Evaluate Module).

---

## Shared Constraints

- Organism: *C. elegans* only (NCBI taxid 6239)
- Vector: L4440 with **HindIII** (forward adapter `CCCAAGCTT`) + **SalI** (reverse adapter `GGGGTCGAC`)
- BLAST: uses NCBI remote service; requires network access
- Runtime: ~2–5 min per gene (BLAST is the bottleneck)
- Machine: default `worker-0` is sufficient (< 100 MB RAM)

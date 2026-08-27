#!/usr/bin/env python3
"""generate_infographic.py — Phylo-branded infographics for the TESLA-guided
neoantigen prioritization skill.

WHY THIS FILE EXISTS
--------------------
`GenerateImage` is an *agent tool*, not a Python API you can import and call from
inside a script. So this module does the two halves a script CAN own:

  1. `build_prompt(...)` / `build_section_prompt(...)`  -> return the EXACT,
     ready-to-send prompt string (Phylo palette + Inter Tight typography + a
     schematic panel layout). The agent (or the skill workflow) passes this string
     to the `GenerateImage` tool with model="gpt-image-2", aspect_ratio="4:3".

  2. `postprocess(png_in, png_out)` -> the required crop/frame QC step: drop any
     leaked footer strip and close a 3px black frame, so every render is a uniform
     asset.

SEVEN INFOGRAPHICS
------------------
One OVERALL workflow infographic (A->B->C pipeline) plus one per CORE ANALYSIS
STAGE of the report:

    overall    — the full Somatic VCF + HLA-I + RNA-seq -> tiered neoantigens flow
    inputs     — the three real inputs (VCF, HLA-I, expression)
    peptides   — neo-peptide generation (missense 8-11mers + indel/frameshift neoORFs)
    binding    — MHCflurry pMHC-I binding (presentation %rank)
    features   — the seven TESLA features across two axes (presentation + recognition)
    tiering    — composite score -> Tier 1/2/3 kept vs excluded
    benchmark  — validation against the real TESLA neoepitope dataset

Every section shares ONE skeleton (`_compose`): title strip, Phylo palette block,
Inter-Tight typography rules, a section-specific MAIN DIAGRAM body, a 3-bullet
evidence footer, and the flat-schematic REQUIREMENTS block. Sections differ only
in their `body` spec and their (REAL) evidence bullets, held in the `SECTIONS`
registry.

CONCEPTUAL / SCHEMATIC FIGURES MUST USE `GenerateImage`, NOT matplotlib — this is
a hard requirement of the platform visualization guidelines. Do not hand-draw any
of these infographics with plotting code.

REAL-DATA-ONLY: every evidence strip uses only numbers produced by real runs of
this pipeline (verified Pt22 melanoma-patient demo + real TESLA benchmark). If you
regenerate for a *different* case, pass real counts/metrics via the `evidence=[...]`
argument; never invent values to fill a strip.

USAGE (agent side)
------------------
    import generate_infographic as gi

    # overall workflow infographic
    prompt = gi.build_prompt()                       # == build_section_prompt("overall")
    # -> call GenerateImage(prompt=prompt, file_name="workflow_infographic.png",
    #                       model="gpt-image-2", aspect_ratio="4:3")
    gi.postprocess("workflow_infographic.png",
                   "assets/figures/workflow_infographic.png")

    # one infographic per core stage
    for key in gi.list_sections():                   # overall, inputs, peptides, ...
        p = gi.build_section_prompt(key)
        # -> GenerateImage(prompt=p, file_name=f"infographic_{key}.png", ...)
        gi.postprocess(f"infographic_{key}.png",
                       f"assets/figures/infographic_{key}.png")

CLI
---
    python3 generate_infographic.py --list                    # list section keys
    python3 generate_infographic.py --section binding         # print that prompt
    python3 generate_infographic.py --print-prompt            # overall prompt
    python3 generate_infographic.py --postprocess IN OUT
"""
from __future__ import annotations

import argparse
import sys

# --------------------------------------------------------------------------- #
# Phylo palette (from the attached infographic template) — use EXACTLY these.
# --------------------------------------------------------------------------- #
PHYLO_PALETTE = {
    "background": "#FAF9F3",   # warm off-white canvas
    "black":      "#000000",   # line-work + primary text
    "magenta":    "#CC2FB2",   # the "engine" / active-computation accent
    "green":      "#75A025",   # retained / prioritized (Tier) outcome
    "orange":     "#FF9400",   # excluded / filtered-out state
    "cream":      "#ECE9E2",   # secondary fills
    "light_pink": "#FD9BED",   # highlight halo (binding / key step)
    "blue":       "#0279EE",   # tiny link / reference markers
}
FONT = "Inter Tight"           # ONLY font allowed anywhere in the image
ASPECT_RATIO = "4:3"           # renders ~1536x1024 with gpt-image-2
MODEL = "gpt-image-2"

# Default evidence bullets for the OVERALL infographic — REAL numbers from
# verified runs (primary demo: real melanoma patient Pt22, Hugo 2016, 6 HLA-I
# alleles; benchmark: real TESLA neoepitope set, 714 peptides).
DEFAULT_EVIDENCE = [
    "Primary demo (Pt22, Hugo 2016 melanoma patient): 8009 candidate peptides from 214 scored missense variants across 6 HLA-I alleles",
    "TESLA benchmark (714 peptides, 33 immunogenic): presentation AUROC 0.78, ~7.6x enrichment in top-20",
    "Real-data-only: peptide-MHC-I binding via MHCflurry; DNA VAF + CCF + tumor RNA-seq are all real; missing measurements stay blank, never imputed",
]

DEFAULT_TITLE = "TESLA-Guided Neoantigen Prioritization"
DEFAULT_SUBTITLE = ("Somatic VCF + HLA-I + RNA-seq  ->  ranked, tiered neoantigens  |  "
                    "MHCflurry pMHC-I + 7 TESLA features (presentation + recognition)  |  "
                    "benchmarked vs TESLA (Cell 2020)")


# --------------------------------------------------------------------------- #
# Shared prompt building blocks
# --------------------------------------------------------------------------- #
def _palette_block() -> str:
    return "\n".join([
        f"- {PHYLO_PALETTE['background']} background (warm off-white)",
        f"- {PHYLO_PALETTE['black']} black for all line-work and primary text",
        f"- {PHYLO_PALETTE['magenta']} magenta — the analysis ENGINE: peptide generation, "
        f"MHCflurry binding, TESLA feature scoring (active-computation accent + key arrows)",
        f"- {PHYLO_PALETTE['green']} green — RETAINED / prioritized output (Tier 1/2/3 kept candidates)",
        f"- {PHYLO_PALETTE['orange']} orange — EXCLUDED / filtered-out peptides (non-binder, low-abundance)",
        f"- {PHYLO_PALETTE['cream']} cream — secondary fills / panel backgrounds",
        f"- {PHYLO_PALETTE['light_pink']} light pink — highlight halo on the key MHCflurry binding step",
        f"- {PHYLO_PALETTE['blue']} blue — tiny link / reference markers only",
    ])


def _typography_block() -> str:
    return (
        f"TYPOGRAPHY — use ONE font only:\n"
        f"- ALL text in the image (title, panel titles, every label, leader-line call-out, and the "
        f"footer\n  bullets) must be set in the \"{FONT}\" typeface — a tight, modern geometric "
        f"sans-serif. Do NOT use\n  any other font; no serif, slab, handwritten, or decorative type "
        f"anywhere in the image."
    )


def _footer_block(evidence) -> str:
    ev = (list(evidence) + ["", "", ""])[:3]
    return (
        f"FOOTER EVIDENCE STRIP (~13%): three short bullet lines in small black text, each preceded "
        f"by a small\nmagenta {PHYLO_PALETTE['magenta']} bullet, laid out in a single horizontal row "
        f"(3 columns side-by-side,\nNOT stacked vertically):\n"
        f"  - \"{ev[0]}\"\n"
        f"  - \"{ev[1]}\"\n"
        f"  - \"{ev[2]}\""
    )


def _requirements_block() -> str:
    return (
        "REQUIREMENTS:\n"
        "- All text must be REAL, readable, correctly spelled English. No garbled words. Spell "
        "\"MHCflurry\",\n  \"neoORF\", \"TESLA\", \"AUROC\", and \"HLA-I\" exactly as written.\n"
        f"- FONT: set every piece of text in the \"{FONT}\" typeface ONLY — no other font anywhere.\n"
        "- Thin clean black line-work for all icons/diagrams. Annotations like a textbook figure.\n"
        "- Use leader lines and call-outs, NOT speech bubbles.\n"
        "- No 3D rendering, no photorealism, no gradients, no shadows. Flat schematic only.\n"
        "- Tight spacing — this is a SHORT WIDE card, NOT a tall poster. Pack content horizontally.\n"
        "- Do NOT include any footer text, signature, watermark, logo, or \"schematic — not to "
        "scale\"\n  disclaimer. The bottom edge of the canvas ends with the evidence footnote strip.\n"
        "- Crisp, accurate molecular/computational iconography."
    )


def _compose(title: str, subtitle: str, kind: str, body: str, evidence) -> str:
    """Assemble a full GenerateImage prompt from the shared skeleton + a
    section-specific MAIN DIAGRAM `body`.

    `kind` is a short phrase describing the figure type (e.g. "COMPUTATIONAL
    PIPELINE", "INPUT DATA", "SCORING") used in the opening framing line.
    """
    return f"""A detailed scientific {kind} infographic, schematic / diagrammatic style \
(a "Cell Reviews" or "Nature Reviews" methods schematic — precise, line-art-forward, labeled \
arrows, clarity). This is a COMPUTATIONAL / METHODS figure (NOT a drug mechanism, NOT decorative).

TITLE (top header strip, ~12% of canvas, single line tall):
- Left, bold black: "{title}"
- Right, same line, smaller grey/black: "{subtitle}"

LANDSCAPE 4:3 format (wider than tall). Background must be {PHYLO_PALETTE['background']} warm off-white.

PHYLO PALETTE only (use EXACTLY these hex colors, no others):
{_palette_block()}

{_typography_block()}

{body}

{_footer_block(evidence)}

{_requirements_block()}"""


# --------------------------------------------------------------------------- #
# Section MAIN-DIAGRAM bodies
# --------------------------------------------------------------------------- #
def _body_overall() -> str:
    return f"""MAIN DIAGRAM (~75% of canvas) — HORIZONTAL 3-PANEL schematic arranged LEFT -> CENTER -> RIGHT (not
stacked). Thin black connector arrows between panels showing flow A -> B -> C:

  PANEL A (LEFT THIRD) — "INPUTS":
  Three clean stacked document / data icons in a vertical column, each with a short label to its right
  via a thin leader line:
    1) a file icon labeled "Somatic VCF" with a smaller sub-label "SNVs + indels/frameshifts"
    2) a chromosome/HLA icon labeled "HLA-I genotype" with sub-label "e.g. HLA-A*02:01 (6 alleles)"
    3) a bar-chart icon labeled "RNA-seq expression" with sub-label "gene-level TPM"
  Draw the three icons in black line-art on cream {PHYLO_PALETTE['cream']} rounded cards.
  Tiny title under panel: "A. Inputs (real data)"

  PANEL B (CENTER THIRD) — "ENGINE" (the KEY panel):
  A vertical mini-flow of three labeled steps connected by short black arrows, INSIDE a cream card:
    step 1: "Neo-peptide generation" — a small DNA->protein motif; sub-label
            "8-11mers spanning the mutation; indel neoORFs translated to new stop"
    step 2: "MHCflurry pMHC-I binding" — draw a small MHC-I groove holding a short peptide, with a
            {PHYLO_PALETTE['light_pink']} light-pink HALO around this binding step to mark it as the
            key engine; sub-label "presentation %rank, best across alleles"; a small magenta
            {PHYLO_PALETTE['magenta']} arrow labeled "score every peptide x allele"
    step 3: "Seven TESLA features (two axes)" — a compact vertical list grouped under two small
            sub-headers. Under a "PRESENTATION" sub-header list: "binding affinity",
            "tumor abundance (TPM x VAF)", "binding stability". Under a "RECOGNITION" sub-header
            list: "agretopicity (mut vs WT %rank)", "foreignness / dissimilarity-to-self",
            "fraction hydrophobic", "mutation position". Then a magenta arrow labeled
            "weighted composite priority score"
  Tiny title under panel: "B. Engine — MHCflurry + TESLA features"

  PANEL C (RIGHT THIRD) — "PRIORITIZED OUTPUT":
  Show a small ranked stack / funnel splitting into two visually OBVIOUS groups:
    TOP group (green {PHYLO_PALETTE['green']} thin border): a short ranked list of KEPT tiers, labeled
      "Tier 1", "Tier 2", "Tier 3" (green = prioritized candidates for validation), with a green arrow
      annotation "ranked shortlist".
    BOTTOM group (orange {PHYLO_PALETTE['orange']} thin border): a smaller grayed box labeled
      "Excluded" with sub-labels "non-binder" and "low abundance (TPM/VAF floor)", with an orange
      arrow annotation "filtered out".
  Make the split between kept (green) vs excluded (orange) VISUALLY OBVIOUS.
  Tiny title under panel: "C. Tiered neoantigens"
"""


def _body_inputs() -> str:
    return f"""MAIN DIAGRAM (~75% of canvas) — HORIZONTAL 3-PANEL schematic arranged LEFT -> CENTER -> RIGHT, the
THREE REAL INPUTS the pipeline consumes. Each panel is a cream {PHYLO_PALETTE['cream']} rounded card
with a black line-art icon at top and a short labeled spec list below via thin leader lines. A thin
black brace on the RIGHT edge gathers all three into a single magenta {PHYLO_PALETTE['magenta']} arrow
labeled "-> one specimen -> prioritization engine".

  PANEL A (LEFT) — "Somatic VCF":
  A document/file icon with a small DNA base-pair motif. Labeled spec list:
    - "SNVs + indels / frameshifts"
    - "consequence-annotated (VEP): HGVSc / HGVSp + transcript id"
    - "germline removed by population frequency (gnomAD), NOT tumor-vs-normal"
  Tiny title under panel: "A. Variants"

  PANEL B (CENTER) — "HLA-I genotype":
  A chromosome / MHC icon. Labeled spec list:
    - "class-I alleles, e.g. HLA-A*02:01"
    - "6 alleles (A/B/C, two each)"
    - "supplied, or typed upstream (OptiType / arcasHLA)"
  Tiny title under panel: "B. HLA-I"

  PANEL C (RIGHT) — "RNA-seq expression":
  A small bar-chart icon. Labeled spec list:
    - "gene-level abundance (TPM or RPKM)"
    - "joined by gene symbol OR Ensembl id"
    - "enables tumor-abundance feature + Tier-1 expression gate"
  Tiny title under panel: "C. Expression"

Below the three panels, a single thin magenta note line, centered:
  "All three layers from the SAME sample -> mutant-allele expression is measurable"
"""


def _body_peptides() -> str:
    return f"""MAIN DIAGRAM (~75% of canvas) — a LEFT -> RIGHT schematic of how candidate neo-peptides are built
from a variant, splitting into TWO parallel tracks that merge into one candidate pool.

  LEFT: a single black "Somatic variant" file icon with a small DNA motif, a magenta
  {PHYLO_PALETTE['magenta']} arrow splitting to the right into TWO tracks (upper + lower).

  UPPER TRACK — "Missense substitution":
    A short amino-acid ribbon with ONE residue highlighted in magenta (the mutated residue). Show a
    small sliding window bracket generating overlapping peptides. Labels:
      - "8-11mers spanning the mutated residue"
      - "wild-type residue validated against the real protein"
    Small sub-caption: "substitution neoepitopes"

  LOWER TRACK — "Indel / frameshift":
    A DNA strand with a small insertion/deletion mark, then a reading-frame shift arrow into a NEW
    protein ribbon drawn in a different shade up to a small red "STOP" box. Labels:
      - "neoORF: translate shifted transcript CDS to the new stop codon"
      - "tumor-specific junction peptides missense-only pipelines MISS"
    Small sub-caption: "neoORF / frameshift neoepitopes"

  RIGHT: both tracks converge with black arrows into a single cream {PHYLO_PALETTE['cream']} card
  labeled "Candidate peptide pool", with a green {PHYLO_PALETTE['green']} count badge
  "8009 candidates" and a smaller sub-badge "all missense (this SNV-only patient yields no neoORF)".

Keep the two tracks clearly parallel and equally weighted; the neoORF track is a first-class pipeline
capability (exercised by a separate synthetic indel fixture), not an afterthought, even though the
primary demo patient happens to carry no indels.
"""


def _body_binding() -> str:
    return f"""MAIN DIAGRAM (~75% of canvas) — a LEFT -> RIGHT schematic of peptide-MHC-I binding prediction, with
the MHC-I binding event as the visual centerpiece (this is the KEY engine step).

  LEFT — "Inputs to binding":
  A small grid/matrix icon labeled "every candidate peptide x every HLA-I allele", sub-label
  "8009 peptides x 6 alleles". A magenta {PHYLO_PALETTE['magenta']} arrow points right, labeled
  "MHCflurry (Apache-2.0, commercial-clean)".

  CENTER — "pMHC-I binding" (CENTERPIECE, wrapped in a {PHYLO_PALETTE['light_pink']} light-pink HALO):
  Draw a clean line-art MHC-I molecule with its peptide-binding GROOVE holding a short peptide chain
  (small circles for residues). Label the groove "MHC-I". A leader line from the peptide reads
  "presentation percentile rank (%rank)". A small sub-note: "best %rank across the 6 alleles is kept".

  RIGHT — "Binding output":
  A compact vertical ranked list of peptides sorted by %rank (small horizontal bars, shorter bar =
  stronger binding). The TOP bar is green {PHYLO_PALETTE['green']} and annotated
  "strongest binder, %rank 0.0017". Lower bars fade toward cream. A tiny axis label reads
  "%rank (lower = stronger presentation)".

Below the panels, one thin black note line, centered:
  "MHCflurry is REQUIRED — with no engine the run hard-fails and emits no numbers (never imputed)"
"""


def _body_features() -> str:
    return f"""MAIN DIAGRAM (~75% of canvas) — a schematic of the SEVEN TESLA features organized into TWO AXES that
feed one weighted composite score. Layout: two stacked labeled groups on the LEFT/CENTER, converging
with magenta {PHYLO_PALETTE['magenta']} arrows into a single score node on the RIGHT.

  TOP GROUP — a cream {PHYLO_PALETTE['cream']} card headed "PRESENTATION axis (weight 0.60)".
  Inside, three rows, each a feature name + a short horizontal weight bar + its weight number:
    - "binding affinity — 0.30"
    - "tumor abundance (TPM x VAF) — 0.22"
    - "binding stability — 0.08"
  Draw these three weight bars in green {PHYLO_PALETTE['green']}.

  BOTTOM GROUP — a cream card headed "RECOGNITION axis (weight 0.40)".
  Inside, four rows, feature name + weight bar + weight number:
    - "agretopicity (mut vs WT %rank) — 0.15"
    - "foreignness / dissimilarity-to-self — 0.13"
    - "fraction hydrophobic — 0.06"
    - "mutation position — 0.06"
  Draw these four weight bars in magenta {PHYLO_PALETTE['magenta']}.

  RIGHT — both group cards feed magenta arrows into a single rounded node labeled
  "weighted composite priority score", sub-label "renormalised over available features, x100".
  A small note beside it: "missing feature -> excluded from the sum, never imputed".

Make the bar lengths roughly proportional to the weights so the eye reads binding affinity as the
largest single contributor. The two axis headers must be clearly the two organizing groups.
"""


def _body_tiering() -> str:
    return f"""MAIN DIAGRAM (~75% of canvas) — a FUNNEL / SANKEY-style schematic: one candidate pool on the LEFT
splitting by the composite priority score into KEPT tiers (green) vs EXCLUDED sets (orange) on the
RIGHT. Widths should roughly reflect the real counts.

  LEFT — a single cream {PHYLO_PALETTE['cream']} block labeled "Candidate pool", green count badge
  "8009 peptides". A magenta {PHYLO_PALETTE['magenta']} arrow labeled "rank by composite score +
  apply binding / expression gates" points right into the split.

  RIGHT (TOP, green {PHYLO_PALETTE['green']} bordered) — KEPT tiers, three stacked bars whose widths
  scale with count, each with a count badge:
    - "Tier 1 — 67  (strong binder + expressed + non-anchor)"
    - "Tier 2 — 276 (binder)"
    - "Tier 3 — 695 (weak)"
  Green arrow annotation on this group: "ranked shortlist for validation".

  RIGHT (BOTTOM, orange {PHYLO_PALETTE['orange']} bordered) — EXCLUDED sets, two stacked grayed bars:
    - "excluded: non-binder — 4993"
    - "excluded: low abundance — 1978"
  Orange arrow annotation on this group: "filtered out".

Make the KEPT (green) vs EXCLUDED (orange) split visually OBVIOUS. A tiny caption under the funnel:
  "top candidate: PDGFRA R376Q IRYQSKLKL on HLA-C*06:02, composite 63.2"
"""


def _body_benchmark() -> str:
    return f"""MAIN DIAGRAM (~75% of canvas) — a VALIDATION / BENCHMARK schematic in three parts LEFT -> CENTER ->
RIGHT, showing how the scoring model is tested against a real external truth set.

  LEFT — "Truth set":
  A document icon labeled "TESLA neoepitope dataset (Cell 2020)", cream {PHYLO_PALETTE['cream']} card,
  sub-labels:
    - "714 peptides with experimental T-cell labels"
    - "33 immunogenic / 681 non-immunogenic"
  A magenta {PHYLO_PALETTE['magenta']} arrow labeled "score with the same model" points right.

  CENTER — "Discrimination (ROC)":
  A small ROC-curve panel: square axes labeled "true positive rate" (y) and "false positive rate"
  (x), a diagonal grey chance line, and one green {PHYLO_PALETTE['green']} ROC curve bowing above it.
  A call-out box reads "presentation AUROC 0.78" with a smaller line "full composite 0.77".

  RIGHT — "Top-list enrichment":
  A small bar-chart panel with two bars: a short grey bar labeled "base rate 4.6%" and a much taller
  green {PHYLO_PALETTE['green']} bar labeled "top-20 enrichment 7.6x". A leader line reads
  "immunogenic peptides concentrate at the top of the ranking".

Below the three panels, one thin black note line, centered:
  "reproduces the central TESLA finding: strong MHC-I presentation dominates immunogenicity"
"""


# --------------------------------------------------------------------------- #
# Section registry
# --------------------------------------------------------------------------- #
# Each entry: title, subtitle, figure-kind phrase, body() builder, and up to 3
# REAL evidence bullets (verified Pt22 melanoma-patient demo + real TESLA benchmark numbers).
SECTIONS = {
    "overall": {
        "title": DEFAULT_TITLE,
        "subtitle": DEFAULT_SUBTITLE,
        "kind": "WORKFLOW / PIPELINE",
        "body": _body_overall,
        "evidence": DEFAULT_EVIDENCE,
    },
    "inputs": {
        "title": "Inputs — Real Data In",
        "subtitle": "Somatic VCF + HLA-I genotype + gene-level RNA-seq, all from one specimen",
        "kind": "INPUT DATA",
        "body": _body_inputs,
        "evidence": [
            "Pt22 demo inputs: 247 real somatic variants (GRCh38) + 6-allele HLA-I genotype + tumor RNA-seq (TPM)",
            "All three layers from the same patient, so mutant-allele expression (TPM x VAF) is measurable",
            "Germline removed by gnomAD population frequency (no matched normal shipped); real-data-only, nothing fabricated",
        ],
    },
    "peptides": {
        "title": "Neo-peptide Generation",
        "subtitle": "Missense 8-11mers + indel/frameshift neoORF junction peptides",
        "kind": "SEQUENCE / PEPTIDE-GENERATION",
        "body": _body_peptides,
        "evidence": [
            "Pt22 demo: 8009 candidate peptides generated from 214 scored missense variants (8-11mers spanning the mutated residue)",
            "This SNV-only patient carries no indels, so it yields no neoORF peptides; the neoORF path is exercised by a separate synthetic fixture",
            "neoORFs translate the shifted transcript CDS to the new stop — antigens missense-only pipelines miss",
        ],
    },
    "binding": {
        "title": "MHCflurry pMHC-I Binding",
        "subtitle": "Presentation percentile rank for every peptide x HLA-I allele",
        "kind": "BINDING-PREDICTION",
        "body": _body_binding,
        "evidence": [
            "Pt22 demo: 8009 peptides scored against 6 HLA-I alleles with MHCflurry (Apache-2.0)",
            "Best presentation %rank kept across alleles; strongest binder at %rank 0.0017",
            "MHCflurry is required — with no engine the run hard-fails and emits no numbers (never imputed)",
        ],
    },
    "features": {
        "title": "Seven TESLA Features, Two Axes",
        "subtitle": "Presentation (weight 0.60) + Recognition (0.40) -> weighted composite score",
        "kind": "SCORING / FEATURE",
        "body": _body_features,
        "evidence": [
            "Presentation axis (0.60): binding affinity 0.30 + tumor abundance 0.22 + binding stability 0.08",
            "Recognition axis (0.40): agretopicity 0.15 + foreignness 0.13 + fraction hydrophobic 0.06 + position 0.06",
            "Composite = weights renormalised over available features (x100); missing feature dropped, never imputed",
        ],
    },
    "tiering": {
        "title": "Tiering — Kept vs Excluded",
        "subtitle": "Composite score + binding/expression gates -> Tier 1/2/3 vs excluded",
        "kind": "PRIORITIZATION / TIERING",
        "body": _body_tiering,
        "evidence": [
            "Pt22 demo from 8009 candidates: Tier 1 = 67, Tier 2 = 276, Tier 3 = 695",
            "Excluded: 4993 non-binders + 1978 low-abundance peptides filtered out",
            "Top candidate: PDGFRA R376Q IRYQSKLKL on HLA-C*06:02, composite priority 63.2",
        ],
    },
    "benchmark": {
        "title": "Benchmark vs Real TESLA Data",
        "subtitle": "Validated against the TESLA consortium neoepitope dataset (Cell 2020)",
        "kind": "VALIDATION / BENCHMARK",
        "body": _body_benchmark,
        "evidence": [
            "Real TESLA truth set: 714 peptides with experimental T-cell labels (33 immunogenic)",
            "Presentation sub-score AUROC 0.78 (full composite 0.77) separating immunogenic from non-immunogenic",
            "~7.6x enrichment of immunogenic peptides in the top-20 vs a 4.6% base rate",
        ],
    },
}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def list_sections() -> list[str]:
    """Return the ordered list of section keys."""
    return list(SECTIONS.keys())


def build_section_prompt(section: str,
                         title: str | None = None,
                         subtitle: str | None = None,
                         evidence: list[str] | None = None) -> str:
    """Return the ready-to-send GenerateImage prompt for one section.

    `section` : one of `list_sections()` (overall, inputs, peptides, binding,
                features, tiering, benchmark).
    `title` / `subtitle` / `evidence` : optional overrides. `evidence` must be REAL
                numbers from an actual run — never invented.
    """
    if section not in SECTIONS:
        raise KeyError(f"unknown section '{section}'; choose from {list_sections()}")
    spec = SECTIONS[section]
    return _compose(
        title=title if title is not None else spec["title"],
        subtitle=subtitle if subtitle is not None else spec["subtitle"],
        kind=spec["kind"],
        body=spec["body"](),
        evidence=evidence if evidence is not None else spec["evidence"],
    )


def build_prompt(title: str = DEFAULT_TITLE,
                 subtitle: str = DEFAULT_SUBTITLE,
                 evidence: list[str] | None = None) -> str:
    """Return the OVERALL workflow-infographic prompt (backward-compatible).

    Equivalent to `build_section_prompt("overall", ...)`. `evidence` is an optional
    list of up to 3 short REAL bullet strings for the footer; defaults to verified
    numbers from this pipeline's demo + benchmark. NEVER pass invented values.
    """
    return build_section_prompt("overall", title=title, subtitle=subtitle, evidence=evidence)


def postprocess(png_in: str, png_out: str, crop_footer: bool = False) -> str:
    """Close a 3px black frame around the render (and, optionally, crop a leaked
    trailing strip).

    Mirrors the framing step from the Phylo infographic template so every render is
    a uniform, cleanly-bordered asset. Requires Pillow + numpy. Returns `png_out`.

    IMPORTANT: the evidence FOOTER strip (the 3 real-number bullets at the bottom of
    each infographic) is INTENDED content and must be preserved. Earlier versions
    auto-cropped everything below the last full-width rule; but these prompts draw a
    horizontal divider *above* the footer, so that logic destroyed the real numbers.
    Cropping is therefore OFF by default. Set `crop_footer=True` only if a specific
    render leaks a genuine duplicate/signature band *below* the intended footer, and
    verify the result with a media check afterwards.
    """
    from PIL import Image
    import numpy as np

    img = Image.open(png_in).convert("RGB")
    arr = np.array(img)
    H, W, _ = arr.shape

    if crop_footer:
        bg = np.array([0xFA, 0xF9, 0xF3])
        diff = np.abs(arr.astype(int) - bg).sum(axis=2)
        ink = (diff > 30)
        row_ink = ink.sum(axis=1)
        full_width_rows = np.where(row_ink > 0.95 * W)[0]
        separators = full_width_rows[full_width_rows < H - 5]
        # only treat a very-near-bottom rule (>93% height) as a leaked band, so we
        # never eat the legitimate evidence strip (which sits ~87-99% height).
        has_footer = len(separators) > 0 and separators[-1] > H * 0.93

        if has_footer:
            cut = int(separators[-1]) - 5
            cream = np.full((H, W, 3), [0xFA, 0xF9, 0xF3], dtype=np.uint8)
            cream[:cut] = arr[:cut]
            cream[cut:, :3] = [0, 0, 0]
            cream[cut:, -3:] = [0, 0, 0]
            arr = cream

    arr[-3:] = [0, 0, 0]
    arr[:3] = [0, 0, 0]
    arr[:, :3] = [0, 0, 0]
    arr[:, -3:] = [0, 0, 0]
    Image.fromarray(arr).save(png_out, optimize=True)
    return png_out


def _cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--print-prompt", action="store_true",
                    help="Print the overall (workflow) GenerateImage prompt to stdout.")
    ap.add_argument("--section", metavar="KEY",
                    help=f"Print the prompt for one section {list_sections()}.")
    ap.add_argument("--list", action="store_true", help="List section keys and exit.")
    ap.add_argument("--postprocess", nargs=2, metavar=("IN", "OUT"),
                    help="Crop/frame a rendered PNG (footer removal + 3px frame).")
    ap.add_argument("--title", default=None)
    args = ap.parse_args(argv)

    if args.list:
        for k in list_sections():
            print(f"{k}\t{SECTIONS[k]['title']}")
        return
    if args.postprocess:
        out = postprocess(args.postprocess[0], args.postprocess[1])
        print(f"[infographic] wrote {out}")
        return
    if args.section:
        sys.stdout.write(build_section_prompt(args.section, title=args.title))
        sys.stdout.write("\n")
        return
    # default: overall prompt
    sys.stdout.write(build_prompt(title=args.title or DEFAULT_TITLE))
    sys.stdout.write("\n")


if __name__ == "__main__":
    _cli()

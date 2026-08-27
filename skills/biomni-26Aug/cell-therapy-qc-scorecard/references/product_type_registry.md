# Product-Type Registry (human-readable)

The machine-readable registry lives in `scripts/product_type_registry.py`
(imported by `derive_marker_panels.py`). This page documents what it contains,
how resolution works, and how to extend it.

## How a target cell type is resolved

`resolve_target_key(target_cell)` maps free text → a registry key by (1) exact
match, (2) alias substring match, (3) token overlap. If nothing matches, the key
is empty and `derive_marker_panels.resolve_panels()` falls back to CellMarker2 and
then to agent-supplied `literature_markers`.

Each registry entry provides:

| Field | Used by | Meaning |
|-------|---------|---------|
| `identity_anchor` | Module A | small RAW-expression identity panel (the anchor; **no** score gate) |
| `fidelity_lineages` | Module A | off-lineage programs that grade target-cell fidelity |
| `offtarget_exclude` | Module C | global off-target lineages to drop (they *are* the target) |
| `maturity` | Module D | `{mature, immature}` axis; empty `{}` → Module D off |

Shared panels (not per-target): `PLURIPOTENCY_PANELS` (Module B),
`OFFTARGET_PANELS` (Module C), `PROLIFERATION_MARKERS` (Module D helper).

## Targets currently covered

| Key | Aliases (examples) | Maturity axis? | Source types |
|-----|--------------------|----------------|--------------|
| `nk` | NK, iNK, CAR-NK, natural killer | yes | ipsc / esc / primary |
| `tcell` | T cell, CD8 T, CAR-T, TCR-T | yes | ipsc / esc / primary |
| `macrophage` | macrophage, iMac, CAR-M, monocyte | yes | ipsc / esc / primary |
| `cardiomyocyte` | cardiomyocyte, cardiac, CM | yes | ipsc / esc |
| `hepatocyte` | hepatocyte, HLC, liver | yes | ipsc / esc |
| `beta_cell` | beta cell, sc-beta, islet | yes | ipsc / esc |
| `neuron` | neuron, cortical/motor/dopaminergic | yes | ipsc / esc |
| `msc` | MSC, mesenchymal, stromal | no (D off) | ipsc / esc / primary |
| `rpe` | RPE, retinal pigment epithelium | no (D off) | ipsc / esc |

The `nk` panels (identity anchor, fidelity lineages, and maturity axis) are the
validated reference set from the iPSC-NK release run. The others are curated from
canonical lineage markers and are a strong starting point; **confirm/extend them
with `LiteratureSearch` for the specific product** and pass additions via
`literature_markers`.

## When Module B (residual pluripotency) runs

Module B runs when `cfg['source']` is `ipsc` or `esc`. `setup_qc_release.infer_source()`
guesses the source from the product description (keywords: iPSC / induced
pluripotent / reprogrammed / "iX-derived" → ipsc; ESC / embryonic stem → esc;
else primary). Override with `source=` in `build_config`.

A **primary**-cell product (e.g. donor-derived CAR-T) does not get Module B —
there are no reprogrammed cells to revert. Its safety modules are identity, off-
target, and technical QC (and maturity if relevant).

## Adding a new target cell type

1. Add an entry to `TARGET_REGISTRY` in `scripts/product_type_registry.py` with an
   `identity_anchor` (5–8 canonical markers), `fidelity_lineages` (2–4 plausible
   off-lineages), `offtarget_exclude` (drop the target's own lineage), and a
   `maturity` axis if one exists.
2. Add aliases so `resolve_target_key` matches real user phrasing.
3. Prefer 5–8 highly specific identity markers over long lists — the anchor uses
   "≥1 detected," so noisy panels inflate purity.
4. Validate on a known-good lot: purity should be high and off-target low for a
   clean product; if not, the anchor or fidelity panels need tightening.

## Marker specificity guidance

- **Identity anchor:** effector/structural markers that are near-exclusive to the
  target lineage (e.g. TNNT2/MYH6 for cardiomyocytes, INS/IAPP for beta cells).
- **Avoid pan-markers** in the anchor (e.g. PTPRC, VIM) — they match many cells.
- **Fidelity lineages:** the lineages a *failed differentiation* of this target
  most plausibly drifts toward.
- **Maturity axis:** mature = terminal-function genes; immature = progenitor/
  proliferation genes. If the literature has no consensus axis, leave `maturity`
  empty (Module D turns off) rather than guessing.

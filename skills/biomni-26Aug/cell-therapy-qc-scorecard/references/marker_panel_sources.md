# Marker Panel Sources — CellMarker2, LiteratureSearch, GEO fetch

How `derive_marker_panels.py` builds panels, and the two agent-run steps
(literature grounding and GEO loading) that the scripts cannot do by themselves.

## Resolution order (per panel)

1. **Curated registry** (`scripts/product_type_registry.py`) — fast, validated,
   the source of truth for covered targets. Always tried first.
2. **CellMarker2 datalake** — fills gaps for target cell types not in the registry.
3. **LiteratureSearch** — the AGENT runs it and passes results via
   `literature_markers=` to add/confirm product-specific markers.

## CellMarker2

`cellmarker2_lookup(cell_type, species)` reads the species table, finds rows whose
`cell_name` contains the query, and returns the most frequently reported marker
symbols. It is **best-effort**: if CellMarker2 has no cell-name matches, it returns
`[]` and the caller falls back to the registry/literature.

CellMarker2 is broad but noisy (aggregated from many studies), so:
- Use it to *propose* markers, then trim to the most specific 5–8 for an anchor.
- Prefer the curated registry when the target is covered.

## LiteratureSearch grounding (agent step)

Run `LiteratureSearch` to confirm or extend panels and thresholds for the specific
product, especially for:
- **Identity anchors** of less-common targets.
- **Maturity axes** (immature→mature markers) — often product- and protocol-specific.
- **Residual-pluripotency assay expectations** and realistic detection limits.
- **Release-threshold precedents** (there is rarely a single universal number).

Then pass results into `resolve_panels(..., literature_markers={...})`. Accepted keys:
`identity_anchor`, `maturity_mature`, `maturity_immature`, `pluripotency_specific`,
`pluripotency_core`, `offtarget_extra` (a `{lineage: [genes]}` dict).

**Cite grounded facts** with the `[N]` indices `LiteratureSearch` returns, and pass
the reference list to `generate_report(..., references=[{'n':N,'text':...}])`. The
report inserts them verbatim; it never fabricates citations. Verify any quantitative
or citation detail against the source before putting it in the report.

Reference facts established for the iPSC-NK reference run (illustrative — re-verify
for a new product): residual-iPSC assays (ddPCR/highly-sensitive qPCR) reach a
limit of detection around ~0.001–0.01%, far below scRNA-seq sensitivity, and there
is no single universal numeric release threshold across products — criteria are set
per product with the sponsor.

## GEO loading (agent step, handled by load_units.py)

`load_units(cfg)` downloads a GEO series when `cfg['inputs']` is a single `GSE#####`
accession. The reliable per-file route is the **acc-based URL**:

```
https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSMxxxx&format=file&file=<url-encoded-filename>
```

(the raw FTP route is flaky). `load_geo_units` enumerates samples via GEOparse, then
downloads either a per-sample `.h5` (CellRanger) or a `matrix.mtx(.gz)` + barcodes +
features trio per GSM, and loads each sample as a unit. For exotic layouts, download
manually to `/workspace/` and use `load_local_units([...])`.

**Combined-reference genomes:** studies that align a human product xenografted into
mouse to a combined `GRCh38_mm10` reference produce species-prefixed gene symbols
(e.g. `GRCh38_GNLY`, `mm10_Actb`). `harmonize_species.py` splits these, keeps the
product-species cells (`human_frac > keep_species_frac`), and strips the prefix so
marker panels match. Set `cfg['multispecies']=True` (or let Step 2 auto-detect when
both prefixes are present).

## Engineering / transgene detection

If `cfg['engineering']` names a transgene (e.g. "MSLN-CAR", reporter EGFP), and that
transgene is present as a feature in the matrix, `score_modules.py` flags
`transgene_pos` cells for reporting. This is descriptive QC (did the transgene get
captured?), not a release gate.

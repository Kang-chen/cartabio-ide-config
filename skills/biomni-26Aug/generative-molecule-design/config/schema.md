# Configuration schema

The pipeline is driven by a single YAML config (see `example_drd2.yml`). All keys
below; defaults in parentheses.

## `target`, `target_desc`
`target` (str) — short label used in filenames and the report title.
`target_desc` (str) — one-sentence target biology for the report Introduction.

## `activity` — the pluggable activity backend
`backend`: one of
- **`tdc_oracle`** — pretrained TDC oracle (offline). `tdc_oracle.name` is any TDC
  oracle (`DRD2`, `GSK3B`, `JNK3`, ...). Scores already in [0,1]. The convenience
  case; no training data needed.
- **`qsar`** — train a quick RandomForest surrogate on your own labelled data.
  `qsar.actives_csv` / `qsar.inactives_csv` each have a `smiles` column. Use when
  no TDC oracle exists for your target (e.g. a ChEMBL export split at pChEMBL ≥ 6).
  This is a *convenience surrogate, not a validated model* — report it as such.
- **`vina`** — AutoDock Vina docking (documented extension point). `vina.receptor_pdbqt`,
  `vina.center`, `vina.box_size`. Slow but most general; wrap the kcal/mol score in
  a `reverse_sigmoid` transform.

## `objective` — the scoring function
Either a **preset name**:
- `production` (recommended) — `geom_mean(activity[identity], QED[identity], SA_Score[reverse_sigmoid])`.
  Gates on makeability so the GA cannot drift toward unsynthesizable molecules.
- `drd2_benchmark` — bare `sqrt(activity × QED)` (geometric mean of the two).
  Reproducibility/benchmark preset; **no** synthesizability term.

…or an **explicit spec**:
```yaml
objective:
  aggregation: geometric_mean     # geometric_mean | weighted_sum | product
  components:
    - {property: <name>, transform: <t>, params: {...}, weight: <w>}
```
- `property` — `activity` (from the backend) or any of
  `QED, SA_Score, MW, LogP, TPSA, HBD, HBA, RotB`.
- `transform` — maps the raw value to [0,1]:
  - `identity` — clamp to [0,1] (for oracles/QED already in range).
  - `sigmoid` `{low, high, k}` — increasing; higher raw = better.
  - `reverse_sigmoid` `{low, high, k}` — decreasing; lower raw = better (SA, MW/LogP ceilings).
  - `range` `{low, high, soft}` — 1 inside the window, ramping to 0 over `soft` outside.
- `weight` — relative weight (default 1.0).

**Why transforms + geometric mean:** a naive weighted arithmetic sum over *raw*
values is broken — MW ≈ 350 numerically swamps QED ≈ 0.7. Every component must be
mapped to [0,1] first. The geometric mean then makes every objective a soft gate:
one near-zero component drives the whole score toward zero, so the optimizer must
satisfy all objectives simultaneously (the REINVENT-4 design).

## `seeds`
`seeds.smiles` (list) or `seeds.seeds_csv` (path, `smiles` column). Known actives
that (a) seed the initial GA population and (b) define the novelty reference set.

## `ga`
`pop_size` (100), `n_generations` (20), `mutation_rate` (0.5), `elite_frac` (0.1),
`tournament_k` (3), `seed` (42).

## `select` — filtering cascade
`novelty_max` (0.4) max ECFP4 Tanimoto to any known active; `activity_min` (0.5);
`qed_min` (0.6); `qed_min_fallback` (0.5) relaxed gate used only if too few
survive; `sa_max` (4.5) Tier-1 synthesizability hard filter; `drop_alerts` (true)
drops PAINS matches (Brenk kept as a flag, not a drop); `require_ring_sanity`
(true); `top_n` (10).

## `retro` — Tier-2 retrosynthesis (heavy, skippable)
`enabled` (true); `cache_dir` (`/mnt/shared-workspace/aizynth_models`) — **provision
models here at setup, not lazily**; `time_limit` (120 s/molecule); `iteration_limit`
(200). If models are missing or egress is blocked, the stage **skips cleanly** and
the report falls back to the SA_Score proxy.

## `output`
`output.dir` — all data/figures/report are written under this directory.

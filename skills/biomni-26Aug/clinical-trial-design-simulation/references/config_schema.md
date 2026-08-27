# Config schema

A single JSON config drives the whole pipeline (`run_pipeline.R`) and the PDF
(`build_report.py`). Top-level blocks: `design`, `validation`, `grid`,
`sensitivity` (optional), `runtime`, `report`. See the three runnable examples in
`scripts/config_examples/`.

Any key not supplied falls back to the engine default shown below.

## `design` — the trial + adaptation parameters

### Shared (all endpoints)
| Key | Default | Meaning |
|---|---|---|
| `endpoint` | `"tte"` | `"tte"` \| `"binary"` \| `"continuous"` |
| `prevalence` | `0.4` | biomarker-positive fraction; **`1.0` ⇒ single full-population hypothesis** (H_S == H_F). This one field defines the analysis population: at `1.0` (or `allow_enrich=false`) the report/figures show a single "Power"/"Type-I rate" and never mention a subgroup, H_S, "any", enrichment, or a closed test; at `<1` with `allow_enrich=true` they show the {H_F, H_S} closed test. |
| `single_hypothesis` | (inferred) | optional boolean that **overrides** the inference above (force single- vs two-hypothesis report presentation). Rarely needed. |
| `N_max` | `300` | maximum sample size (subject pool for TTE) |
| `info_frac` | `0.5` | information fraction of the (single) interim analysis. The report's Methods text describes the interim timing directly from this field, so prose and simulation always agree. |
| `spending` | `"asOF"` | alpha-spending: `asOF`, `asP` (Pocock), `asKD`, ... (rpact names) |
| `alpha` | `0.025` | one-sided significance level |
| `accrual_months` | `24` | uniform accrual window |
| `dropout_rate` | `0.05` (tte) / `0` | exponential dropout rate (per the accrual/follow-up scale). The exact value is stated in the report's Methods, so the reported and simulated dropout always match. |

### Adaptation switches
| Key | Default | Meaning |
|---|---|---|
| `allow_efficacy` | `false` | permit early efficacy stopping at the interim (else only final look rejects) |
| `allow_futility` | `true` | permit conditional-power futility stopping |
| `allow_enrich` | `true` | permit adaptive population enrichment to the biomarker+ subgroup |
| `allow_ssr` | `false` | permit conditional-power sample-size re-estimation |
| `enrich_delta` | `0.5` | enrich if `z_S,cum - z_F,cum` exceeds this at the interim |
| `futility_cp` | `0.10` | stop for futility if interim conditional power < this |
| `ssr_cp_target` | `0.90` | conditional power the SSR tries to reach |
| `ssr_cp_min` / `ssr_cp_max` | `0.30` / `0.90` | SSR only fires when interim CP is in this "promising" window |
| `ssr_nmax_cap` | `2.0` | maximum inflation factor on the final target (N or events) |

### Time-to-event (`endpoint: "tte"`)
| Key | Default | Meaning |
|---|---|---|
| `median_ctrl` | `18.9` | control-arm median (months); rate = `log(2)/median` |
| `target_events` | `169` | **event-driven** target (primary information unit) |
| `max_followup` | `48` | administrative follow-up cap after last accrual (months). Set large (e.g. 1000) for event-driven designs that must reach the target |
| `dist` | `"exponential"` | `"exponential"` or `"weibull"` (with `weibull_shape`, default 1.2) |
| `hr_pos` / `hr_neg` | `0.65` | **design/effect** hazard ratio in biomarker+/− (see effect keys below) |

### Binary (`endpoint: "binary"`)
| Key | Default | Meaning |
|---|---|---|
| `p_ctrl` | `0.20` | control-arm response probability |
| effect keys | — | `p_trt_{pos,neg}` (direct), or `rr_{pos,neg}` (risk ratio), or `or_{pos,neg}` (odds ratio) |

### Continuous (`endpoint: "continuous"`)
| Key | Default | Meaning |
|---|---|---|
| `mean_ctrl` | `0` | control-arm mean |
| `sd` | `1` | common standard deviation (so `delta` is a Cohen's d) |
| `higher_is_better` | `true` | direction of benefit |
| effect keys | — | `mean_trt_{pos,neg}` (direct) or `delta_{pos,neg}` (mean difference) |

## Effect specification (per subgroup `slot` = `pos` / `neg`)
Each scenario sets the experimental effect **by subgroup**. Resolution priority:
- **tte:** `hr_{slot}`
- **binary:** `p_trt_{slot}` > `rr_{slot}` > `or_{slot}` > (no effect)
- **continuous:** `mean_trt_{slot}` > `delta_{slot}` > (no effect)

A "uniform" effect sets pos == neg. A "subgroup-only" effect sets a benefit in
`pos` and null in `neg`.

## `validation`
| Key | Default | Meaning |
|---|---|---|
| `enforce` | `true` | if a gate fails, STOP (no OC/report) |
| `power_tol` | `0.02` | Gate 2 tolerance on \|sim − rpact\| |
| `seed_fwer` / `seed_power` | `100` / `500` | RNG seeds for the two gates |
| `fwer_null_variants` | `[]` | list of `{label, ...overrides}` extra null configs (e.g. prevalence extremes) |
| `power_grid` | endpoint default | `{hr_grid}` / `{p_trt_grid}` / `{delta_grid}` for Gate 2 |

## `grid` — operating-characteristic scenarios
```json
"grid": {
  "seed": 10,
  "scenarios": {
    "Scenario label": { "hr_pos": 0.60, "hr_neg": 0.85 },
    ...
  }
}
```
Each key is a scenario **label** (used verbatim in tables/figures); each value
overrides the `design` effect keys. Always include a null scenario.

## `sensitivity` (optional)
```json
"sensitivity": {
  "param": "prevalence",
  "values": [0.2, 0.3, 0.4, 0.5, 0.6],
  "scenario": { "hr_pos": 0.55, "hr_neg": 1.00 },
  "seed": 50
}
```
Sweeps a single design/effect `param` over `values`, holding `scenario` fixed.
Feeds the sensitivity figures.

## `runtime`
| Key | Default | Meaning |
|---|---|---|
| `preset` | `"quick"` | `"quick"` (nsim = 1000 OC / 2000 gates) or `"thorough"` (10000) |
| `ncores` | `4` | parallel cores for simulation |

The CLI third argument overrides `runtime.preset`:
`Rscript run_pipeline.R config.json out_dir thorough`.

## `report` — narrative + branding (consumed by build_report.py)
| Key | Meaning |
|---|---|
| `title`, `subtitle` | header text |
| `headline_scenario` | scenario label used to fill the infographic power/N cards. Keep it consistent with `effect_label` (they describe the same target effect). |
| `effect_label` | short target-effect string for the infographic — **the single source of truth** for the target effect. Put it here (report-level). If `design.effect_label` is also given and **differs**, the report build fails with an error so the effect size is specified once and unambiguously. |
| `bottom_line` | optional override of the bottom-line callout (else auto from gate status) |
| `introduction`, `methods`, `conclusions`, `next_steps` | optional lists of paragraphs (else sensible defaults are generated) |
| `oc_intro`, `recommendation` | optional narrative snippets |
| `figures` | optional list of `[filename, caption]`; defaults to the 5 standard figures |
| `references` | list of citation strings (rendered as the References section) |

Rich text in narrative strings uses ReportLab XML tags (`<b>`, `<i>`, `<sub>`,
`<super>`) — **not** markdown and **not** Unicode sub/superscripts.

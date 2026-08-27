# Aggregation propensity rules (named, sequence-based: AGGRESCAN a3v)

Implemented in `scripts/ab_core.py` (`aggregation_scan`, `aggregation_profile`,
constants `A3V`, `A3V_HST`, `MIN_APR_LEN`) and rolled up by
`scripts/developability_scan.py` / `scripts/reassess_constructs.py`. This is the
skill's **aggregation-risk metric**, and it deliberately uses a **named,
published predictor** (AGGRESCAN) rather than a hydrophobicity/charge surrogate.

## Why AGGRESCAN a3v (and not GRAVY/charge)

Earlier versions of this skill described aggregation only through GRAVY
(Kyte-Doolittle hydropathy), pI and net charge. Those are generic physicochemical
descriptors, not an aggregation predictor, and they did not even enter construct
ranking. They are now retained purely as **descriptive context**
(`biophysical()`), while aggregation risk is scored with the **AGGRESCAN**
aggregation-propensity scale:

- **Scale (a3v / aaAV):** per-residue intrinsic aggregation propensities derived
  from an **in vivo** Abeta42 GFP-fusion mutational assay — Sanchez de Groot et
  al., *FEBS J* 2006;273:658-668 — and used by the AGGRESCAN predictor —
  Conchillo-Sole et al., *BMC Bioinformatics* 2007;8:65. More positive = more
  aggregation-prone. The paper's ranking (Ile, Phe, Val, Leu highest; Asp, Glu,
  Asn, Arg lowest) is reproduced exactly by the embedded table.

The exact a3v values used (verified against three independent public
implementations):

```
I  1.822   F  1.754   V  1.594   L  1.380   Y  1.159
W  1.037   M  0.910   C  0.604   A -0.036   T -0.159
S -0.294   P -0.334   G -0.535   K -0.931   H -1.033
Q -1.231   R -1.240   N -1.302   E -1.412   D -1.836
```

## The algorithm (as implemented)

1. **a3v assignment** — each residue gets its a3v value.
2. **Virtual terminal residues** — to emulate the charged termini (NH3+/COO-), a
   virtual residue is added at each end: N-terminus = mean a3v of the basic
   residues (K, R); C-terminus = mean a3v of the acidic residues (D, E).
3. **Windowed profile (a4v)** — the a3v values are averaged over a sliding window
   centred on each residue. Window size is **length-adaptive** per AGGRESCAN:
   5 for <=75 aa, **7 for <=175 aa** (the variable-domain case), 9 for <=300,
   11 for >300. Edge positions that cannot centre a full window take the nearest
   full-window value.
4. **Hot-spot threshold (HST)** — `A3V_HST` = the average a3v over the 20 amino
   acids (~ -0.02). (The original server uses a SwissProt-frequency-weighted mean;
   the paper text defines the simpler 20-AA mean, which is what is used here for a
   transparent, dependency-free implementation. Note the deviation if you switch.)
5. **Aggregation-prone regions (APRs / "hot spots")** — contiguous runs of
   **`MIN_APR_LEN` = 5 or more** consecutive residues that (a) each have a4v above
   the HST and (b) contain **no proline**. Proline is an aggregation/beta-breaker
   in AGGRESCAN: a Pro inside an otherwise-hot stretch splits it into separate
   candidate segments, each of which must independently reach the 5-residue
   minimum. (Proline still contributes its a3v value to the windowed a4v profile;
   the breaker rule governs only hot-spot membership, so `agg_score` is
   unaffected.) This 5-residue-minimum + proline-breaker definition is the
   AGGRESCAN hot-spot rule.

## Reported quantities

Per variable domain (and rolled up to the Fv):

- `agg_score` / `agg_score_Fv` — the mean a4v over the domain (mean of VH & VL for
  the Fv). The reproducible **headline intrinsic-propensity** value (can be
  negative; higher = more aggregation-prone on average).
- `n_APR` — number of aggregation-prone regions.
- `APR_in_FR` / `APR_in_CDR` — APRs split by region. **Framework-resident APRs are
  the actionable humanization target**; CDR-resident APRs are inherited from the
  donor (CDRs are grafted verbatim) and are the residual, affinity-constrained
  risk to address by targeted mutation.
- `agg_weighted` — **CDR-weighted aggregation burden**: for each APR, the
  excess-propensity area (sum of `a4v - HST` over the region) is summed, with
  CDR-resident APRs up-weighted **1.6x** (the same `cdr_weight` as the liability
  scan, for the same reason: a hot-spot in a paratope loop matters more). **This
  is the value used for ranking** in the master frontier and scorecard.
- The per-APR table adds `residues`, `position` (Kabat span), `region`,
  `location` (CDR/FR), `length`, `peak_a4v`, `area`, `weighted_area`.

## Scope and honest limitations (state these in any report)

AGGRESCAN a3v is a **sequence-based** intrinsic-propensity predictor. It:

- does **not** use a 3D structure, so it may flag stretches that are buried in the
  native fold, and
- misses **3D aggregation patches** formed by residues distant in sequence but
  close in space, and colloidal/charge-network effects.

For a structure-aware confirmation when a folded Fv is available, upgrade to a
**structure-based** predictor — **AggreScan3D** (the structure-based extension of
this same a3v scale; `pip install aggrescan3d`, needs a PDB and, for its dynamic
mode, FoldX) or the **Therapeutic Antibody Profiler (TAP)** via SAbPred. The
report and SKILL.md name this upgrade path; it is not run by default because it
adds a structure-prediction step.

## Interpreting the output for a report

- Lead with the **CDR-weighted aggregation burden** (`agg_weighted`) and the
  **APR count split FR vs CDR**, comparing constructs.
- For a humanized panel, show that grafting onto human frameworks lowers the
  aggregation burden and/or removes framework-resident APRs relative to the
  non-human parent; if a naive graft does **not** lower it, say so honestly rather
  than over-claiming (the back-mutated lead is usually the aggregation winner).
- Enumerate the highest-propensity APRs by **Kabat position** so they are
  addressable in a redesign round; flag CDR-resident APRs as "carried over from
  the donor, candidates for targeted affinity-aware mutation."
- Always disclose the sequence-based scope and the structure-based upgrade path.

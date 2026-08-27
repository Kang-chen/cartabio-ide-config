# Worked example — de novo mini-binders against PCSK9

This is the end-to-end example the skill's helper scripts were validated against.
Every number below was reproduced by the scripts in `scripts/` from the actual
pipeline outputs (dry-run, no new HPC jobs). Use it as a template for a new target.

## Target

- **Protein:** PCSK9 catalytic domain (proprotein convertase subtilisin/kexin type 9)
- **Structure:** PDB `2P4E`; UniProt `Q8NBP7` (RCSB reports X-ray, 1.98 Å — these facts are
  fetched at report time by `fetch_structure_metadata.py`, not hand-typed into the config)
- **Domain used:** catalytic domain, native residues ~155–461 (prodomain removed)
- **Catalytic triad tracked:** Asp186 / His226 / Ser386
- **Rationale:** PCSK9 drives LDL-receptor degradation and is a validated
  hypercholesterolemia target; de novo mini-binders offer small, stable scaffolds
  distinct from approved antibodies.

## Step 0 — target prep (`prepare_target.py`)

```
python scripts/prepare_target.py \
  --in 2p4e.pdb --chain A --range 155-461 \
  --margin 10 --margin-mode spatial --min-segment 4 \
  --key-residues 186,226,386 \
  --out target_pcsk9_cat.pdb
```
- Spatial 10 Å margin around the 155–461 core, then short scattered runs pruned
  (`--min-segment 4`) so the RFdiffusion contig is not fragmented by singletons.
- Emits the `contigmap.contigs=[...]` line used verbatim in the RFdiffusion command.

## Step 1 — RFdiffusion (15 backbones)

- Command: see `references/hpc_commands.md` §1, `inference.num_designs=15`,
  `Complex_base_ckpt.pt`, `noise_scale_ca=0.5`, `noise_scale_frame=0.5`, no hotspots.
- Result: **15 binder backbones**, 60–90 aa, each docked to the catalytic domain
  (6–27 Cα interface contacts across backbones).

## Step 2 — ProteinMPNN (120 sequences)

- Command: see §2, `--num_seq_per_target 4 --sampling_temp '0.1 0.2' --use_soluble_model`.
- Result: **120 designed sequences** (8 per backbone).

## Step 3 — filter + select (`filter_sequences.py`)

```
python scripts/filter_sequences.py \
  --seq-dir <mpnn_out>/seqs \
  --backbone-json rfdiff/backbone_summary.json \
  --out-csv analysis/all_sequences.csv --top-n 6
```
- Filters (validated preset): `entropy >= 2.0`, `top1_frac <= 0.42`, `n_cys <= 2`,
  `interface_contacts >= 8`.
- Result: **83 / 120 pass** (filtered on raw metric values). Best sequence per
  backbone, top 6 backbones selected for topological diversity.

  > Note: an earlier run recorded 84 pass; the difference is a single borderline
  > sequence at entropy ≈ 1.997 that rounds to 2.00 at 2 decimals. Filtering on the
  > raw (unrounded) value is the reproducible choice and gives 83. It does not affect
  > the selected candidates (all have entropy 2.66–3.37).

- Selected candidates (design id -> candidate), with MPNN score (lower = better):

  | candidate | design | len | MPNN score |
  |---|---|---|---|
  | cand1 | 7  | 90 | 0.8949 |
  | cand2 | 0  | 80 | 0.9202 |
  | cand3 | 12 | 89 | 0.9914 |
  | cand4 | 1  | 82 | 1.0180 |
  | cand5 | 5  | 85 | 1.0217 |
  | cand6 | 3  | 80 | 1.0650 |

## Step 4 — Boltz-2 co-folding (6 candidates)

- Command: see §3. Submitted in waves (max 3 GPU jobs). YAML per candidate:
  binder = chain A (single sequence), target = PCSK9 catalytic domain = chain B.

## Step 5 — interface analysis (`analyze_interface.py`)

```
python scripts/analyze_interface.py \
  --candidates-csv cand_specs.csv \
  --domain-start 155 --catalytic-triad 186,226,386 \
  --out-json all_candidate_metrics.json --out-csv boltz2_validation_ranking.csv
```
`--domain-start 155` recovers native PCSK9 numbering (Boltz renumbers the target 1..N).

**Ranking (reproduced bit-exactly by the script; ranked by ipTM):**

| rank | cand | ipTM | iface PAE (Å) | contacts | binder iface | target iface | tier |
|---|---|---|---|---|---|---|---|
| 1 | cand2 | 0.9466 | 1.76 | 170 | 17 | 20 | HIGH |
| 2 | cand3 | 0.6104 | 8.85 | 172 | 13 | 15 | MODERATE |
| 3 | cand1 | 0.5383 | 12.87 | 200 | 21 | 21 | LOW |
| 4 | cand4 | 0.4487 | 15.09 | 195 | 19 | 20 | LOW |
| 5 | cand6 | 0.2288 | 21.26 | 158 | 15 | 14 | LOW |
| 6 | cand5 | 0.1118 | 27.36 | 168 | 13 | 12 | LOW |

**Winner: cand2** — the only HIGH-confidence design (ipTM 0.9466, interface PAE 1.76 Å).

Top-candidate sequences:
- cand2 (80 aa): `AEEERARLLELAELHRELAELLRELAEELRKKMEEAVKESEDEEEAKKLKEKYEKEIEEAERNARRAEEEAARLRAEAEA`
- cand3 (89 aa): `MAEIEELEEWLREAEERAKEKEEELKRLEEEAEEVRRKAEEDESKREELEKKAKELEEKARLLREELEQLRDEAEVARRLRRELEEAAR`
- cand1 (90 aa): `MKEEKIKELEEKAEELRKEAEELDKKAEEKWEEAERLRREAAEASPEEAERLLKEAEEKEKEAQELLEKAAELTKKYLELKEEAERLREE`

## Convergent epitope

Nine PCSK9 residues are contacted by **all three** top binders:
**260, 261, 262, 265, 292, 293, 294, 295, 296**. None overlap the catalytic triad
(186/226/386) — the binders engage a surface patch, not the (prodomain-blocked)
active site.

## MPNN-score vs ipTM correlation (interpretation — read carefully)

Raw Spearman(mpnn_score, ipTM) = **−0.77** (n = 6, p = 0.07). Because **lower** MPNN
score is better and **higher** ipTM is better, a **negative** raw correlation means
the two metrics **agree** (better designs also fold with higher confidence). The
top-3 set {cand1, cand2, cand3} and bottom-3 set {cand4, cand5, cand6} are the same
under both metrics; only the fine ordering within the top-3 differs (cand1 is the
best MPNN score but 3rd by ipTM; cand2 wins on ipTM). This is exactly why Boltz-2
co-folding is used as an orthogonal filter to pick the final winner.

> Reporting tip: annotate figures/reports with the **raw** sign (−0.77) so it matches
> a mpnn-vs-ipTM scatter, then explain "negative = agree". Do NOT report only
> "+0.77 quality-aligned" without the plot context — it reads as contradicting a
> visibly downward-sloping scatter.

## Deliverables produced

- `boltz2_validation_ranking.csv` — ranked table (source of truth)
- `all_candidate_metrics.json` — full per-candidate metrics + sequences + epitope
- `fig_mpnn_scores`, `fig_boltz_metrics`, `fig_interfaces` (PNG + SVG)
- PDF report: `build_report.py` runs the consistency gate and emits the validated, style-free
  report content (`report_content.json`); the final PDF is rendered from it via the
  pdf-report-generation skill (which owns the report styling). Before building, fetch the structure
  facts: `python scripts/fetch_structure_metadata.py --pdb 2P4E --out structure_metadata.json`,
  then pass `--structure-metadata structure_metadata.json --construct-scope construct_scope.json`.
  Resolution/method/deposition come from that fetched sidecar (with provenance) and the construct
  span comes from `construct_scope.json` — neither is hand-typed in the config.

## Limitations

Computational designs only. ipTM/PAE report predicted-geometry confidence, not
measured affinity. Next steps are experimental: expression, SPR/BLI binding, and a
functional/competition assay. The charged-residue-rich sequences (a ProteinMPNN
tendency) warrant solubility/developability review before synthesis.

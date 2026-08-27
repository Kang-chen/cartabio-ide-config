# Method reference: parameters, box sizing, metric definitions

## Docking parameters

**Screening (per ligand):** AutoDock Vina `--exhaustiveness 8 --num_modes 9 --seed 42
--cpu 1`. One CPU per ligand; throughput comes from many ligands across many cores/workers.
Rank compounds by the **best-mode** affinity (mode 1), where **more negative = better**.

**Redock validation:** higher search effort — `--exhaustiveness 16 --num_modes 20 --seed 42`
— because we want the best shot at reproducing the known pose, and it is only one ligand.

**Score parsing:** take mode 1 from either the Vina stdout results table (a 4-field line whose
first token is `1`; affinity is token 2) or from the output PDBQT (`REMARK VINA RESULT`,
4th token). Both are implemented in `common.py`.

## Box sizing

- Prefer defining the box from a **co-crystal ligand**: center = ligand centroid, edge =
  max(ligand extent + 2*padding, min_size). Defaults padding 4 A, min 16 A → a ~16-22 A cube.
- A box that is too large degrades both accuracy (more decoy pose space) and speed
  (exhaustiveness must rise to compensate). Keep it pocket-scale.
- If there is no native ligand, define the box from a pocket detector or explicit
  `--center X Y Z --size S`, and treat redock validation as unavailable (state it).

## Ligand preparation

`MolFromSmiles → AddHs → ETKDGv3(randomSeed=seed) → EmbedMolecule (retry with
useRandomCoords=True on failure) → MMFFOptimizeMolecule(maxIters=500) → Meeko
MoleculePreparation().prepare() → PDBQTWriterLegacy.write_string()`. One protonation /
tautomer / conformer per ligand — a documented simplification. Target ≥95% prep success;
failures (bad valence, embedding failure) are logged in `ligand_prep_log.csv`.

## Redock pose criterion (core-aware, two-tier, auditable)

- Compute **whole-molecule** heavy-atom RMSD and a **rigid/buried core** RMSD; **pass** if
  either is < threshold (default 2.0 A). Passing on the core alone is legitimate when a
  solvent-exposed tail moves.
- **Core definition (principled, recorded):**
  - `mcs` — ring atoms + linker atoms bonded to ≥2 ring atoms (the rigid scaffold).
  - `buried` — atoms within `--contact` A (default 4.5) of any receptor heavy atom (a
    low-SASA proxy that needs no SASA engine).
  - `mcs+buried` (default) — the intersection (rigid AND buried); falls back to whichever is
    non-empty if the intersection is empty.
  - `--core-smarts` — explicit substructure override.
  - The **exact atom indices** used are written to `redock_validation.json` so the criterion
    is auditable, not hand-picked.
- **RMSD is symmetry-corrected** over molecular automorphisms
  (`GetSubstructMatches(mol, mol, uniquify=False)`) so symmetric substructures don't inflate
  it. Coordinates are read from the PDBQT in **file order** (H skipped) to avoid fragile
  atom-count round-trips.
- **Warn, do not hard-fail.** A failed redock is *information* (wrong box/protonation/receptor
  or a genuinely bad pose model). The script always exits 0, records `passed:false` + a
  warning, and the report must surface it in Limitations.

## Enrichment metrics (labeled branch)

Convention: **actives = 1, decoys = 0**; rank **ascending** by `vina_affinity` (most negative
first). For ROC, score = `-vina_affinity` (so higher = better, standard orientation).

- **ROC-AUC** — probability a random active outranks a random decoy. 0.5 = random.
- **BEDROC (alpha=20)** — Boltzmann-enhanced early recognition; weights the top of the list.
  alpha=20 emphasizes ~top 8%. Ranges 0-1; ~0.05 is random-ish for balanced-ish sets.
- **EF at f%** — (actives in top f%) / (actives expected by chance). EF=1 is random.
- Computed with RDKit's validated `rdkit.ML.Scoring.Scoring` (`CalcAUC`, `CalcBEDROC`,
  `CalcEnrichment`), which expect rows sorted **best→worst** with the label in a column; we
  pass `[[label], ...]` and col 0. (Do not hand-roll these — RDKit's implementation is the
  authoritative one; a naive BEDROC is easy to get wrong.)
- **Class separation:** Mann-Whitney U on affinities, `alternative="less"` (actives more
  negative), with a **rank-biserial effect size** `rbc = 1 - 2U/(nA*nD)`. Report the effect
  size next to p — with large n, p is tiny even for a trivial effect.

## Triage & SAR

- **Precision@N** (labeled): fraction of the top-N that are known actives, compared to the
  baseline active rate. A meaningful "is the very top useful?" check even when global AUC is
  poor.
- **Descriptors** (RDKit): MW, cLogP, TPSA, HBD, HBA, RotB, AromaticRings, Fsp3, HeavyAtoms,
  QED, Murcko scaffold.
- **Butina clustering:** Morgan FP (r=2, 2048-bit), distance = 1 - Tanimoto,
  `Butina.ClusterData(..., cutoff=0.4, isDistData=True)`, clusters sorted by size. Reveals
  scaffold diversity and lets you cherry-pick diverse hits.
- **Property-affinity Spearman:** rho<0 for MW/cLogP/AromaticRings means the score rewards
  large/lipophilic molecules — the **Vina size/lipophilicity bias**. Flag it so hit lists
  aren't just molecular-weight sorts.

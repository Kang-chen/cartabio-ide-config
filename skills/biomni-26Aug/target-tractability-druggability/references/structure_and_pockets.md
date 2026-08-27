# Structure retrieval & fpocket pocket detection — reference

The structural stream asks: **does the target have a druggable 3D pocket?** It is heuristic and
conformation-dependent — a favorable geometric score is a *hypothesis-generating* signal, not proof
of a viable binding site.

## Structure source priority (auto mode)

1. **Best experimental structure from RCSB PDB.** Query the RCSB Search API for structures mapped
   to the target's **human UniProt accession**, then rank candidates by:
   - **Bound drug-like ligand present** (captures druggable / cryptic pockets that only open in a
     holo form — this is exactly why apo KRAS looks undruggable but the sotorasib-bound switch-II
     pocket scores highly). Prefer holo structures with a non-buffer heteroatom ligand.
   - **Resolution** (lower Å is better; X-ray/cryo-EM).
   - **Chain completeness** (fewest missing residues).
   - When useful, keep BOTH an **apo** and a **holo/drug-bound** structure and contrast them —
     cryptic pockets show up as a large druggability jump from apo → holo.
2. **AlphaFold predicted model** (fallback, when no usable experimental structure exists). Resolve
   the current file URL from the AlphaFold API `https://alphafold.ebi.ac.uk/api/prediction/<UNIPROT>`
   (read `pdbUrl` from the JSON) rather than hardcoding a version — the model-file version changes
   over time (`...-model_v4.pdb` -> `...-model_v6.pdb` -> ...), so a hardcoded URL silently 404s and
   suppresses the fallback for every target. `fetch_structure.py` does this and only falls back to
   guessing versioned URLs if the API is unreachable. Flag clearly in the report that pocket scores
   on a predicted model are **less reliable** (pLDDT-dependent; predicted structures often lack
   ligand-induced/cryptic pockets and can have low-confidence loops).
3. **Skip with a note** only if both fail (e.g. no experimental structure and no AlphaFold entry).
   The report still ships — the structural section states the limitation.

> We do **not** run AlphaFold *prediction* in the default flow. If a user explicitly wants a de-novo
> model for a target with no DB entry, the Biomni HPC AlphaFold2 / ESMFold tools can be used, but
> that is an out-of-band extension, not part of the standard skill run.

### RCSB endpoints
- Search API (POST JSON): `https://search.rcsb.org/rcsbsearch/v2/query`
- File download: `https://files.rcsb.org/download/<PDBID>.pdb`
- Entry metadata (REST): `https://data.rcsb.org/rest/v1/core/entry/<PDBID>`

A robust query maps UniProt → PDB via the search service
(`rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers` /
`rcsb_uniprot_container_identifiers`). If the Search API shape changes, a simple fallback is the
UniProt entry's cross-references (`https://rest.uniprot.org/uniprotkb/<ACC>.json` → `uniProtKBCrossReferences`
with `database == "PDB"`), which also carry resolution and chain metadata.

## PDB cleaning before fpocket

fpocket detects cavities lined by **protein** atoms, so clean the file first:
- Keep a **single chain** (default: the chain with the most residues / the one carrying the ligand).
- **Always drop waters** (`HOH`).
- Optionally **retain one reference ligand** (the bound drug) purely to compute its centroid for
  pocket annotation — but note fpocket pockets are protein-atom-lined and will not "enclose" the
  ligand atoms exactly.
- Write `TER`/`END` records.

Coordinate/field parsing (PDB fixed-column format):
```
record  = line[0:6].strip()        # ATOM / HETATM
chain   = line[21]
resname = line[17:20].strip()
resnum  = int(line[22:26])
x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
```

## Running fpocket

```bash
fpocket -f cleaned_chainA.pdb
# → creates cleaned_chainA_out/ with:
#     cleaned_chainA_info.txt          (per-pocket scores)
#     pockets/pocket1_atm.pdb, pocket2_atm.pdb, ...  (atoms lining each pocket)
```

Install (only if `which fpocket` is empty):
```bash
conda install -n base -y -c conda-forge -c bioconda fpocket   # v4.x
```

## CRITICAL: fpocket pocket files are **1-indexed**

The pocket atom files are named `pocket1_atm.pdb`, `pocket2_atm.pdb`, … — **there is no
`pocket0`**, and these numbers match the "Pocket N :" blocks in the info file directly. **Do NOT add
a +1 offset.** Parse the id straight from the filename:

```python
import re
pid = int(re.search(r"pocket(\d+)_atm", pocket_file).group(1))   # NO offset
```

In the original KRAS run, assuming 0-indexing and adding +1 silently mis-mapped the top pocket to
empty residues and a wrong volume. This is the single most important gotcha in this skill.

## fpocket score interpretation (Druggability Score)

Take the pocket with the **highest Druggability Score** ("drug_score" in the info file).

| Druggability Score | Interpretation |
|---|---|
| **> 0.5** | Druggable pocket |
| **0.2 – 0.5** | Borderline / shallow |
| **< 0.2** | Poorly druggable |

Also record **volume (Å³)**, number of pocket residues, hydrophobicity, and SASA from the info file.

## Pocket annotation (generalized — NOT residue-hardcoded)

Annotate the top pocket relative to references you can compute for *any* target, rather than
hardcoded KRAS residue numbers:
- **Distance to a bound ligand centroid.** If the holo structure has a drug, compute the ligand
  centroid; if the top pocket centroid is close (≲ ~12 Å / overlapping residues), it is the
  **drug-engaged pocket**. If it is far (e.g. > 20 Å), it is a **distinct / potentially allosteric**
  site — report it as such (this is how the KRAS α3/loop-7 allosteric pocket was correctly
  distinguished from the orthosteric surface).
- **Distance to any cofactor/nucleotide** (GTP/GDP/ATP/NAD/etc. heteroatoms in the structure), same
  logic → "orthosteric/cofactor-adjacent" vs "distal".
- **Solvent exposure / volume** → shallow surface groove vs deep enclosed cavity.

Report the top-pocket location descriptively ("drug-engaged pocket", "distinct allosteric-type
pocket ~27 Å from the cofactor", "shallow surface groove") instead of asserting a named functional
site you cannot verify for an arbitrary target. When residue-level functional context IS known for
the specific target (from literature), add it as annotation with a citation.

## Licenses & commercial use (state these accurately in the report)

| Resource | License | Commercial use |
|---|---|---|
| **fpocket** | **MIT License** (verified at [github.com/Discngine/fpocket](https://github.com/Discngine/fpocket) — see the LICENSE file) | Permitted. MIT is a permissive license with no copyleft obligation. The skill calls fpocket as a subprocess, which does not create a derivative work. **Do not state fpocket is GPL v2** — it is MIT. |
| **RCSB PDB** (structure coordinates) | **Public domain** | No restrictions on coordinate data. |
| **AlphaFold DB** (predicted models) | **CC BY 4.0** | Permitted with attribution. |
| **UniProt** (cross-references) | **CC BY 4.0** | Permitted with attribution. |

> **License accuracy note:** A prior candidate run misidentified fpocket as "GNU GPL v2" and Open
> Targets as "CC BY 4.0". Both were wrong. fpocket is **MIT** (more permissive than GPL); Open
> Targets Platform data is **CC0 1.0** (public domain — see `references/opentargets_tractability.md`).
> Always verify the license at the upstream source before stating it in the commercial-dependency
> report; do not rely on memory.

## Honest caveats (put these in the report)

- fpocket scores are **geometry-based and conformation-dependent**. A high score on a drug-bound
  crystal partly reflects that the pocket was captured in its open state.
- Adjacent sub-pockets can **merge** into one fpocket pocket, inflating volume and residue lists.
- AlphaFold-model pockets are **less reliable** than experimental ones.
- A favorable pocket score is **not** a validated druggable site — it is a lead for experimental
  follow-up.

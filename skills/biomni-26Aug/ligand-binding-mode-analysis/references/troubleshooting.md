# Troubleshooting

## "No drug-like ligand found (only ions/buffers/solvent present)"
The structure is likely **apo** (no bound small molecule) or its only HETATMs are
additives.
- Confirm the structure actually has a ligand (check the RCSB entry page).
- If a ligand is present but was filtered, pass it explicitly: `ligand_code="XXX"`.
- If it is genuinely apo, this skill does not apply — use docking (AutoDock Vina)
  or co-folding (Boltz-2 / Chai-1) to place a ligand first.

## Wrong ligand selected
Multiple ligands or a cofactor (ATP/NAD/HEM) present. Force the intended one:
`run_analysis(..., ligand_code="STI")`. Enumerate what's present with:
```python
from find_ligands import load_structure, enumerate_ligands
for lg in enumerate_ligands(load_structure("1IEP.pdb")):
    print(lg["resname"], lg["n_heavy"], "IGNORE" if lg["is_ignored"] else "")
```

## RDKit fragmentation returns everything as "scaffold" / "linker"
RDKit could not sanitize the ligand from its coordinates (unusual bonding,
missing atoms). The pipeline automatically falls back to the graph-based
fragmenter, which gives generic names (ring sizes, heteroatom counts, element
groups). Fragments are still usable for the heatmap; names are just less pretty.

## PyMOL import error / no 3D image
`pymol-open-source` is not installed. Either:
- `conda install -c conda-forge pymol-open-source -y`, or
- accept the automatic **matplotlib fallback** 3D view (a Cα/ligand scatter — lower
  quality but never blocks the pipeline).
The report handles a missing 3D figure gracefully (it is simply omitted).

## 3D labels clipped or overlapping (PyMOL)
The render zooms out with padding to avoid clipping. If labels still crowd for a
very large pocket, reduce the number of `key_residues` passed, or increase the
zoom padding in `render_3d.render_pymol` (`cmd.zoom("ligobj", 12)` → larger value).

## Kinase motifs look wrong
Detection is heuristic (sequence motifs + contact refinement). Causes: atypical
kinase, pseudokinase, truncated construct, insertions in the activation loop, or
non-standard residue numbering. The DFG/HRD/P-loop/β3-Lys/αC-Glu labels are the
most robust; hinge/gatekeeper depend on the ligand being ATP-competitive. Treat
labels as advisory and cross-check with KLIFS/UniProt for important claims. To
disable the kinase layer entirely, ignore the `kinase_region` column.

## Unicode characters render as black boxes in the PDF
ReportLab's Helvetica lacks many glyphs. `build_report` already converts common
ones (αC → &#945;C, β3 → &#946;3, Å → &#197;, ≤/≥). If you add new narrative text,
keep it ASCII or use HTML entities / `<sub>`/`<super>` tags — never raw Unicode
subscripts/superscripts.

## RCSB download fails / times out
- Verify the PDB ID is valid and 4 characters.
- The fetch has a retry loop; transient failures usually resolve on retry.
- Behind a proxy/offline, download the `.pdb` manually and pass the local path.

## Contacts from two protomers appear mixed
The pipeline keeps the dominant contacting chain automatically. If your ligand
truly bridges two chains (rare), analyse each chain separately by pre-filtering the
structure or setting `primary_chain`.

## The report has only 1 page / validation fails
Usually a build error before content was added (check the traceback). `validate_pdf`
asserts ≥2 pages, >5 KB, and extractable text. Re-run after fixing the upstream
error; then run the `media_output_check` visual review.

## Literature section is empty
By design, the skill never fabricates references. In an agent session, call the
**LiteratureSearch** tool and pass real results via `payload["references"]`. If no
search is run, the report states the pocket was not cross-referenced — that is
correct behaviour, not a bug.

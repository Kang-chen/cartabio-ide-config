# Kinase Motif Annotation (optional layer)

When the target is a protein kinase, the skill labels pocket residues by their
canonical catalytic role. This is what makes a kinase ATP-site interpretable and
reproduces the annotations from the imatinib-ABL1 benchmark (gatekeeper Thr315,
hinge Met318, DFG Asp381, etc.). The layer **auto-activates** when a kinase is
detected and is silent otherwise.

## What is labelled

| Region | Role | How detected |
|--------|------|--------------|
| **P-loop** (glycine-rich loop) | Positions the phosphates of ATP; often packs the inhibitor's solvent-facing end | Sequence motif `G-x-G-x-x-G` N-terminal to the catalytic Lys |
| **Catalytic Lys (β3)** | Anchors ATP α/β phosphates; salt bridge to αC-Glu | `[VAIL]-x-K` (VAIK-like) Lys preceding HRD/DFG |
| **αC-helix Glu** | Forms the Lys–Glu salt bridge marking the active (αC-in) state | First Glu 4–33 residues C-terminal to the β3 Lys |
| **Gatekeeper** | Controls access to the back hydrophobic pocket; a key selectivity residue | Residue immediately N-terminal to the first hinge residue that contacts the ligand |
| **Hinge** | Backbone H-bonds to the adenine of ATP and to most inhibitors | Contacting residues between αC-Glu and the catalytic loop that H-bond the ligand |
| **Catalytic loop (HRD)** | Contains the catalytic Asp (proton acceptor) | Sequence motif `H-[RK]-D` N-terminal to DFG |
| **DFG motif** | Asp binds Mg²⁺; Phe packs the regulatory spine; DFG-in/out defines conformation | Sequence motif `DFG` at the activation-loop start |

## Detection strategy (`kinase_annotate.py`)

1. **Kinase call:** scan the ligand-bearing chain for the **HRD** and **DFG**
   motifs. If both are present in the expected order (HRD N-terminal to DFG), the
   target is treated as a kinase. This is conservative — it will not fire on
   non-kinases lacking these motifs.
2. **Motif anchoring:** annotations are anchored to the **detected motif
   positions** in the supplied structure's own numbering — *not* to hard-coded
   ABL1 residue numbers. This is what makes the layer general across kinases.
3. **β3-Lys / αC-Glu:** located relative to the VAIK Lys and the following Glu.
4. **Gatekeeper / hinge refinement:** these two are refined using the **actual
   ligand contacts** — the hinge is the contacting stretch (between αC-Glu and the
   catalytic loop) that hydrogen-bonds the ligand; the gatekeeper is the residue
   just before it. Grounding these in geometry avoids fragile numbering guesses.

## Reliability

- **DFG, HRD, P-loop, β3-Lys, αC-Glu** come from conserved sequence motifs and are
  robust for canonical kinases.
- **Hinge and gatekeeper** are refined from contacts; they are reliable when the
  ligand is ATP-competitive (binds the adenine pocket), which covers most kinase
  inhibitors. For allosteric ligands the hinge/gatekeeper labels may be empty or
  approximate.
- Atypical kinases, pseudokinases, truncated constructs, or non-standard numbering
  can cause mislabeling. **Treat labels as advisory** and confirm against a
  reference (e.g. KLIFS / UniProt) for high-stakes claims.

## Inhibitor typing (interpretation)

Once annotated, the DFG conformation + hinge pattern inform the inhibitor class:
- **Type I** — binds the ATP pocket with the DFG-in (active) conformation; hinge
  H-bonds to the adenine region.
- **Type II** — binds with DFG-out, extending into the back pocket opened by the
  flipped Phe (imatinib is the archetypal type II binder of ABL1).

The skill does not auto-classify type I vs II (that needs conformational analysis),
but flags it as a next step.

# CAR Design Reference

> **Native-first.** Prefer Biomni-native tools/databases for every sourcing step;
> fall back to raw web/REST only when there is no native equivalent. Import
> documented `biomni.tool` functions before promising them — the live package can
> differ from a static inventory (for example, `design_crispr_knockout_guides` is
> not installed in this environment).

## Sequence & part sourcing order (native-first)

Use this precedence to obtain every sequence part (scFv, hinge/TM/costim,
signaling domain, promoter, WPRE, backbone). Take experimentally-validated
sources over reconstructions.

1. **Addgene via Biomni integrations** — for real, deposited CAR plasmids and
   parts. `biomni.tool.integrations`:
   - `search_plasmids(*, name=..., genes=..., purpose=..., species=..., backbone=...,
     vector_types=..., page=1, page_size=20, sort_by='id')` — keyword-only; search
     by e.g. `genes="CD19"` or `name="FMC63"`.
   - `get_plasmid_with_sequences(plasmid_id: int)` — full record + sequences.
   - `get_addgene_sequence_files(plasmid_id: int)` — downloadable sequence files.
2. **RCSB PDB** — for experimentally-determined scFv/antibody structures and their
   chain sequences (query the PDB REST/entry FASTA).
3. **UniProt** — for canonical human domain protein sequences (hinge/TM/costim/
   signaling) when you want the reference isoform.
4. **`fetch_gene_coding_sequence(gene_name, organism, email=None)`**
   (`biomni.tool.molecular_biology`) — returns a list of coding-sequence dicts from
   NCBI; use for gene-level CDS when you need the nucleotide coding sequence of a
   part (e.g., CD247/CD28/TNFRSF9) rather than a canned amino-acid string.
5. **Curated fallback (below)** — the exact validated amino-acid sequences in this
   file, used only when the native routes are unavailable or you need a guaranteed
   reproducible build. Always note in the report which route each part came from.

## scFv provenance (critical)

Always take the antigen-binding scFv from an experimentally-validated source, not
a reconstruction. For the anti-CD19 FMC63 scFv used in all four FDA-approved CD19
CAR-T products, use **PDB 7URV chain D** (FMC63 single-chain variable fragment in
complex with CD19; *Mus musculus*). Fetch its FASTA from RCSB and use the VL/VH
directly. (For a generalized run with a different antigen, search Addgene with
`search_plasmids(genes=<antigen>)` and/or find the scFv/antibody structure in RCSB
PDB first; fall back to a published scFv sequence via `LiteratureSearch`.)

Arrange the scFv as **VL – (G4S)3 linker – VH** (the configuration used in the
approved 4-1BB products). The (G4S)3 linker is `GGGGSGGGGSGGGGS`.

## Second-generation CAR domain order

```
N — signal peptide — scFv(VL-linker-VH) — hinge — transmembrane — costimulatory — CD3z — C
```

- **BBz** (tisagenlecleucel / Kymriah-style): CD8a hinge + CD8a TM + 4-1BB costim.
  Slower, more persistent responses.
- **28z** (axicabtagene ciloleucel / Yescarta-style): CD28 hinge + CD28 TM + CD28
  costim. Faster, more intense effector activity.

Both terminate in the CD3z (CD247) signaling domain.

## Canonical human domain amino-acid sequences

These are the exact sequences used to build the validated FMC63 constructs.

| Domain | Sequence |
|---|---|
| CD8a signal peptide | `MALPVTALLLPLALLLHAARP` |
| (G4S)3 linker | `GGGGSGGGGSGGGGS` |
| CD8a hinge | `TTTPAPRPPTPAPTIASQPLSLRPEACRPAAGGAVHTRGLDFACD` |
| CD8a transmembrane | `IYIWAPLAGTCGVLLLSLVITLYC` |
| 4-1BB (TNFRSF9) costim | `KRGRKKLLYIFKQPFMRPVQTTQEEDGCSCRFPEEEEGGCEL` |
| CD28 hinge | `IEVMYPPPYLDNEKSNGTIIHVKGKHLCPSPLFPGPSKP` |
| CD28 transmembrane | `FWVLVVVGGVLACYSLLVTVAFIIFWV` |
| CD28 costim | `RSKRSRLLHSDYMNMTPRRPGPTRKHYQPYAPPRDFAAYRS` |
| CD3z (CD247) signaling | `RVKFSRSADAPAYQQGQNQLYNELNLGRREEYDVLDKRRGRDPEMGGKPRRKNPQEGLYNELQKDKMAEAYSEIGMKGERRRGKGHDGLYQGLSTATKDTYDALHMQALPPR` |

The CD28 module canonically begins at the `IEVMYPPPY` motif (GenBank NM_006139).

## FMC63 scFv (from 7URV chain D)

- VL (107 aa): `DIQMTQTTSSLSASLGDRVTISCRASQDISKYLNWYQQKPDGTVKLLIYHTSRLHSGVPSRFSGSGSGTDYSLTISNLEQEDIATYFCQQGNTLPYTFGGGTKLEIT`
- VH (120 aa): `EVKLQESGPGLVAPSQSLSVTCTVSGVSLPDYGVSWIRQPPRKGLEWLGVIWGSETTYYNSALKSRLTIIKDNSKSQVFLKMNSLQTDDTAIYYCAKHYYYGGSYAMDYWGQGTSVTVSS`

## Reference construct sizes (sanity check)

- **FMC63-BBz** mature protein = 486 aa. Domain boundaries:
  CD8a signal 1–21; VL 22–128; (G4S)3 129–143; VH 144–263; CD8a hinge 264–308;
  CD8a TM 309–332; 4-1BB 333–374; CD3z 375–486.
- **FMC63-28z** mature protein = 482 aa. Domain boundaries:
  CD8a signal 1–21; VL 22–128; (G4S)3 129–143; VH 144–263; CD28 hinge 264–302;
  CD28 TM 303–329; CD28 costim 330–370; CD3z 371–482.
- BBz codon-optimized ORF ≈ 1,461 nt, GC ≈ 64%.

## Lentiviral transfer cassette layout

```
EFS promoter — Kozak (GCCACC) — CAR ORF (ATG…stop) — WPRE
```

- **EFS** = short intronless EF-1a core promoter (good for lentivirus, less
  silencing than CMV in T cells).
- **WPRE** = woodchuck hepatitis post-transcriptional regulatory element; boosts
  transgene expression.
- Reference cassette sizes: BBz ≈ 3,238 bp, 28z ≈ 3,226 bp.

Emit both a CAR-ORF GenBank (ORF + internal domain features) and a full-cassette
GenBank (promoter/Kozak/ORF/WPRE + internal domain features). Use Biopython
`SeqRecord` + `SeqFeature` and write with `Bio.SeqIO.write(..., "genbank")`.

## Codon optimization

Codon-optimize the full ORF for *Homo sapiens*. A simple, robust approach is to
map each amino acid to its most-frequent human codon (a static human codon-usage
table), preserving the start ATG and appending a stop codon. Avoid introducing
unwanted restriction sites if the user plans a specific cloning strategy. Verify
the translated optimized ORF is identical to the intended protein.

## Wet-lab handoff (native protocol tools)

When the user wants to move from in-silico design toward the bench, pull
standardized protocols/comparisons from `biomni.tool.molecular_biology` (all return
dicts) rather than writing them from memory:

- `get_lentivirus_production_protocol()` — lentiviral packaging/production protocol
  for delivering the CAR transfer cassette.
- `get_facs_sorting_protocol()` — FACS sorting protocol (e.g., isolating
  transduced/CAR+ or edited cells).
- `compare_knockout_cas_systems()` — compares CRISPR knockout Cas systems; use this
  to guide KO strategy for validating screen hits in CAR-T cells. **Note:** there is
  no native guide-design tool in this environment, so source sgRNAs from the
  published screen library, an Addgene knockout library, or a validated published
  guide set — do not claim automated guide design.

Cite these as Biomni-provided protocols in the report Methods/Next-steps and note
they are standardized templates to be adapted to the user's exact cell system.

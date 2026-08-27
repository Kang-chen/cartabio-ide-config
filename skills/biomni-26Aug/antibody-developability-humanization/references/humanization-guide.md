# Humanization & back-mutation guide

Implemented in `scripts/humanize_backmutate.py`. This is the engine that turns a
non-human (murine/other) Fv into humanized candidates by CDR-grafting onto human
acceptor frameworks and then restoring a principled set of framework residues
("back-mutations") to recover binding.

## The strategy in one paragraph

Graft the donor CDRs onto a human germline framework (human FR1-FR3 from the V
gene + human FR4 from the J gene), then selectively **back-mutate** framework
positions from the human residue back to the donor residue **only at positions
known to support the CDR loops or the VH/VL interface** — never inside the CDRs
themselves. The naive graft is maximally human but often under-binds; the
back-mutated variant trades a little humanness for restored affinity. The skill
builds and compares both so the trade-off is explicit.

## Two acceptor philosophies (built by default)

For each chain the engine constructs **two** acceptors and grafts onto both,
because "closest human sequence" and "best-behaved human framework" frequently
disagree:

1. **`consensus`** — the most common / most developable human germline. These are
   the field-standard fixed acceptors:
   - Heavy: `IGHV3-23*01` + `IGHJ4*01` (human VH subgroup III — the most
     expressed, most stable, least aggregation-prone human heavy framework).
   - Kappa: `IGKV1-39*01` + `IGKJ4*01` (human Vκ subgroup I).
   - (Lambda handled analogously when the light chain is λ.)
   This is what most marketed humanized antibodies use, and it is a
   **developability** choice, not a sequence-identity choice.
2. **`nearest`** — the human germline with the **highest framework identity** to
   the query, selected blind via the framework-only identity scan (IMGT FR
   positions FR1 1-26, FR2 39-55, FR3 66-104). Maximizes raw framework identity
   but may land on a less commonly used scaffold.

**Key teaching point:** framework choice matters more than raw germline identity.
In the validated muMAb 4D5 case the nearest germline by framework identity was a
**VH1** gene (~65% FR identity), but the consensus **VH3** acceptor (~59% FR
identity) is the one that reproduced the real clinical answer (trastuzumab is
built on VH3). Reporting both lets the data show which is better for a given
antibody instead of hard-coding one.

This yields (for a non-human paired Fv) five constructs:
`donor`, `hu_consensus_graft`, `hu_consensus_bmut`, `hu_nearest_graft`,
`hu_nearest_bmut`.

## Back-mutation position sets (validated)

Back-mutations are drawn only from curated framework rule sets, never CDRs:

- **Vernier zone** (Foote & Winter 1992, *J Mol Biol*) — framework residues that
  underpin CDR conformation:
  - VH `{2, 27, 28, 29, 30, 48, 49, 67, 69, 71, 73, 78, 80, 93, 94}`
  - VL `{2, 4, 35, 36, 46, 47, 48, 49, 64, 66, 68, 69, 71}`
- **VH/VL interface** — `VH {37, 39, 45, 47, 91, 93, 103}`
- **Canonical-class determinants** (Chothia) — CDR-supporting framework residues:
  - VH `{24, 71, 94}`, VL `{2, 25, 33, 71}`

A back-mutation is applied when the position is in the active rule set(s) **and**
the human graft residue differs from the donor residue. Each is annotated with
which rule(s) fired.

## Aggressiveness levels (`level=`)

`BACKMUT_LEVELS` controls which rule sets are active. **Note the naming is by
humanization aggressiveness (how hard you push toward human), so "aggressive"
means FEWEST back-mutations:**

| level | active rule sets | effect |
|---|---|---|
| `aggressive` | Canonical only | minimal reversions, most human, highest affinity-loss risk |
| `moderate` | Vernier | |
| `conservative` **(default, validated)** | Vernier + Interface + Canonical | most reversions, safest for binding |
| `maximal` | Vernier + Interface + Canonical | same as conservative here |

`DEFAULT_LEVEL = "conservative"` — this is the validated setting and the right
default for a first pass, because losing binding is usually worse than carrying a
few extra non-human framework residues. Drop to `moderate`/`aggressive` only when
you deliberately want a more human sequence and can tolerate affinity-optimization
downstream.

## Custom acceptors / positions

- `acceptors=` lets you supply your own acceptor germlines instead of the two
  defaults.
- `custom_positions=` lets you force specific back-mutation positions (e.g. from
  a customer's structural analysis) on top of the rule-based set.

## Output tables

`humanize()` returns `constructs` (the sequences), plus a `backmutations`
DataFrame with columns `domain, kabat, donor_aa, human_graft_aa, region, rules`
and a `philosophy` column (`consensus`/`nearest`) so the two acceptor families can
be told apart. In the report, back-mutations are filtered to the **lead
construct's philosophy** so the reader sees only the reversions actually in the
lead design.

## How this connects to the reference-present benchmark

When a validated reference antibody exists (e.g. trastuzumab for muMAb 4D5), the
optional benchmark module checks how many of the engine's *blind* back-mutations
match the reference's known framework changes (canonical/Vernier recovery) — a
convergence test. See `report-modes-guide.md`. Crucially, the benchmark is never
used to pick the design; it only scores a design that was built blind.

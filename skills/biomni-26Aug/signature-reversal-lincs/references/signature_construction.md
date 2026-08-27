# Query signature construction

The connectivity query needs a clean up/down gene signature. Prefer a user-provided signature; only
auto-build when the user supplies just a disease/phenotype name.

## Option A — User-provided signature
- **Gene lists:** accept up and down HGNC symbol lists directly.
- **DE table:** threshold into up/down sets at a stated cutoff. Sensible defaults:
  `padj < 0.05 & |log2FC| > 1`, or top-N by |effect| (e.g. top 150/direction) if few pass.
  Always print the cutoff used and the resulting sizes.
- Normalize symbols to current HGNC (alias resolution). Drop unmapped symbols with a note.

## Option B — Auto-build a consensus signature (disease name only)
Local data lake `/mnt/datalake/LINCS1000/RNAseq_transcriptomics_genesets/` provides GMT collections.
**The two files have different structure — pick the method to match:**

| File | Structure (VERIFIED) | Use for |
|---|---|---|
| `disease_signatures-v1.0.gmt` | **CREEDS-style: exactly one `<disease>-up` and one `<disease>-dn` set per disease** (~330 diseases, single study each). NOT multi-study. | Direct single-source signature — the fast default when the disease is listed. |
| `human_GEO.gmt` / `mouse_GEO.gmt` | **Multi-study GEO signatures** (~8,500 sets; a given disease may have many GSE studies, each with up/dn). | True multi-study consensus (e.g. Parkinson's has ~18 sets across GSEs). |

**Method 1 — Direct (use with `disease_signatures-v1.0.gmt`):**
1. Find the `<disease>-up` and `<disease>-dn` lines matching the disease name (case-insensitive;
   try synonyms). This is usually one pair.
2. Apply the technical-gene filter (below). The remaining genes ARE the signature (typically
   200–350/direction; downsample to the top ~150 if you need a tighter query, keeping the filter).
3. Record provenance: source = CREEDS single study. Note in the report that this is a **single-source**
   signature (no cross-study consensus) — a real limitation vs. a user-supplied curated signature.

**Method 2 — Consensus voting (use with `human_GEO.gmt` when the disease has ≥3–4 studies):**
1. Select all up/dn sets whose descriptor matches the disease (case-insensitive; include synonyms —
   e.g. "ulcerative colitis", "colitis", "IBD"). Pair up/dn per study where possible.
2. For every gene, tally `n_up`/`n_dn` = # studies calling it up/down. `net = n_up - n_dn`.
3. Keep genes with **recurrence ≥ 4 studies** and **consistency ≥ 0.6**
   (consistency = max(n_up, n_dn)/(n_up + n_dn)). If a disease has only 2–3 studies, LOWER the
   recurrence floor to ≥2 (and say so) or fall back to Method 1 — do not silently return an empty
   signature.
4. Rank within each direction by (n in that direction, consistency, |net|); take top ~150/direction;
   backfill from the recurrence pool if short.
5. Record provenance: # studies, source GMT, per-gene vote counts (save a `*_gene_scoring.csv`).

**Decision rule:** try `disease_signatures-v1.0.gmt` first (Method 1). If the disease isn't there,
or you want cross-study robustness, use `human_GEO.gmt` (Method 2); if it has <4 studies, drop the
recurrence floor to 2 or revert to Method 1. **Always verify the signature is non-empty and
biologically plausible before querying** — an empty consensus (too-high recurrence floor for a
sparse disease) is a silent failure mode.

## Technical / housekeeping gene filter (apply to either option)
Ubiquitously perturbed technical genes add noise and inflate spurious connectivity. Remove them —
but **do not remove genuine biology**.

Remove:
- Ribosomal proteins: regex `^RP[LS]\d`.
- An explicit technical set (translation factors, chaperones commonly co-perturbed, cytoskeleton /
  housekeeping): `EEF1A1, EEF1G, EEF2, HSP90AA1, HSP90AB1, HSPA8, DNAJA1, YWHAZ, UBC, UBB, GAPDH,
  ACTB, TMSB10, PABPC1, HNRNPK, NCL, NPM1, PTMA, FAU, FTL, FTH1, B2M, NACA, EIF4A1, EIF4A2` and
  mitochondrially-encoded `ND2, COX1, COX2, ...`.

**RETAIN (do NOT filter):**
- **Metallothioneins** `MT1*` / `MT2A` — genuinely regulated in inflammation and many diseases.
- Any disease-relevant gene the user flags.

Always print the removed genes so the choice is auditable. Note that HSP90AA1/AB1 appear in the
technical set for *signature cleaning*; HSP90 *inhibitors* are still valid compound hits — filtering
the gene from the query does not exclude the drug class from results.

```python
import re
TECH = {"EEF1A1","EEF1G","EEF2","HSP90AA1","HSP90AB1","HSPA8","DNAJA1","YWHAZ","UBC","UBB",
        "GAPDH","ACTB","TMSB10","PABPC1","HNRNPK","NCL","NPM1","PTMA","FAU","FTL","FTH1",
        "B2M","NACA","EIF4A1","EIF4A2","ND2","COX1","COX2"}
def is_technical(g):
    return bool(re.match(r"^RP[LS]\d", g)) or g in TECH
# keep metallothioneins explicitly
def keep_gene(g):
    if g.startswith("MT1") or g == "MT2A":
        return True
    return not is_technical(g)
```

## Sanity checks before querying
- Print the top ~20 recurrent up and down genes; they should look biologically plausible for the
  disease (e.g. inflammation/neutrophil markers up, differentiated-cell markers down in active
  colitis). If they look like a generic stress/proliferation program, revisit thresholds.
- Report final sizes (aim ~100–200/direction) and provenance.

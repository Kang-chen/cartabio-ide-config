# Read-Driven Library Reconstruction (the paywall-circumventing technique)

**Problem.** Pooled CRISPR screens need a guide-to-gene table to run MAGeCK. For
many published screens this table is distributed only as a paywalled/gated
supplementary Excel file. Do not give up — if the paper states the guides were
subsetted from a known genome-wide library (e.g., **Brunello**, **GeCKO**,
**Bassik**), you can reconstruct the library directly and reproducibly from the
raw reads + the public reference library.

This technique recovered the Shifrut et al. pilot library (1,209 genes × 4 guides
+ 48 NTCs) and reproduced the paper's showcase hits (CBLB #1, CD3D #1).

## Step 0 — Get the reference library

Download the parent library's guide table (Brunello is public via Addgene /
Broad GPP). You need two columns: the 20-nt spacer ("sgRNA Target Sequence") and
the gene symbol ("Target Gene Symbol"). Build a dict:

```python
spacer2gene = dict(zip(bru["sgRNA Target Sequence"].str.upper().str.strip(),
                       bru["Target Gene Symbol"]))
# Brunello: 77,441 unique spacers, 19,115 genes
```

## Step 1 — Decode read structure (do this first, always)

Pooled-screen reads are short (often ~50 bp) and the 20-nt spacer is flanked by
constant vector sequence — it is almost never at position 0. Inspect:

```bash
zcat sample.fastq.gz | head -20
zcat sample.fastq.gz | awk 'NR%4==2' | head -100000 | cut -c1-51 | sort | uniq -c | sort -rn | head
```

Look for a constant anchor motif shared by most reads. In the Shifrut reads the
structure was: `[1 variable diversity base][19-bp constant ...GCTCTTAAAC][20-bp spacer][trailing constant]`.
The reliable anchor was **`GCTCTTAAAC`** immediately 5' of the spacer.

## Step 2 — Extract the spacer, fix orientation

```python
def rc(s):
    return s.translate(str.maketrans("ACGTN", "TGCAN"))[::-1]

def extract_spacer(seq, anchor="GCTCTTAAAC"):
    i = seq.find(anchor)
    if i < 0:
        return None
    sp = seq[i+len(anchor) : i+len(anchor)+20]   # next 20 nt
    return sp if len(sp) == 20 else None
```

**Critical orientation check.** Reads often contain the REVERSE COMPLEMENT of the
reference spacer. Test both orientations against `spacer2gene` on a sample of
reads and keep whichever matches:

```python
# forward match rate vs. rc match rate on ~50k reads
fwd = sum(sp in spacer2gene for sp in sample_spacers)
rev = sum(rc(sp) in spacer2gene for sp in sample_spacers)
# Shifrut: fwd ~0%, rev ~73% -> reads are RC of Brunello; use rc(sp)
```

Whichever wins, convert every extracted spacer to the reference orientation
before tallying. Expect ~99% of reads to contain the anchor and ~73–76% to map
to the reference library.

## Step 3 — Determine library membership

Tally pooled counts per reference spacer across all samples. A gene is "in the
pilot" if enough of its guides are observed:

```python
# gene in pilot if >=3 of 4 Brunello guides observed at pooled count >= threshold
# then take ALL 4 Brunello guides for those genes (non-circular: use the designed
# guides, not only the observed ones)
```

This gave 1,209 genes × 4 = 4,836 targeting guides. Taking all designed guides
(rather than only observed ones) avoids circularity and matches how the library
was actually built.

## Step 4 — Recover non-targeting controls (NTCs)

NTCs are not in the reference library, so they appear as high-count spacers that
DON'T match. They share a **U6 transcription-start signature: they begin with G**.
Rank all non-reference spacers by pooled count and look for a clean break:

```python
# top non-Brunello spacers by count. The true NTCs form a clean block that is
# ~100% G-start; rank drops sharply after the real NTC count (48 here).
```

In Shifrut, the top 48 non-Brunello spacers were 100% G-start (counts 410–2196)
and rank 49+ dropped to ~54% G-start (counts <=371) — a clean boundary at exactly
48, matching the paper's 48 NTCs. Label them `Non_Targeting_Control` with IDs
`Non_Targeting_Control_01..NN`.

## Step 5 — Write the MAGeCK library file

CSV with columns `sgRNA,sequence,gene` and **NO header**. sgRNA IDs like
`{gene}_{n}`; all sequences 20 nt in the reference orientation.

```
CBLB_1,ACAG..(20nt)..,CBLB
...
Non_Targeting_Control_01,GTGA..(20nt)..,Non_Targeting_Control
```

Also write `ntc_guides.txt` (one NTC sgRNA ID per line) for `--control-sgrna`.

## Validation that reconstruction worked

- Recovered gene count matches the paper (here 1,209 vs. stated 1,211 — within
  the threshold tolerance).
- NTC count matches exactly (48).
- MAGeCK recovers the paper's showcase hits (CBLB, CD5 positive; CD3D, LCP2
  negative). This biological recovery is the real proof.

## Honest caveat for the report

State clearly that the library and NTCs were reconstructed from reads + the
reference library rather than the original supplementary table; it is transparent
and reproducible but may differ from the original at the margins.

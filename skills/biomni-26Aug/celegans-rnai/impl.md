# Implementation Guide: celegans-rnai Skill

---

## Section A — Design Module

### A1. Sequence Retrieval (NCBI Entrez)

```r
library(rentrez)

fetch_celegans_mrna <- function(gene_name) {
  # Search NCBI Gene
  search <- entrez_search(
    db = "gene",
    term = paste0(gene_name, "[Gene Name] AND Caenorhabditis elegans[Organism] AND alive[prop]"),
    retmax = 5
  )
  if (search$count == 0) stop(paste("Gene not found:", gene_name))
  gene_id <- search$ids[1]

  # Get linked mRNA records
  links <- entrez_link(dbfrom = "gene", id = gene_id, db = "nuccore")
  mrna_ids <- links$links$gene_nuccore_refseqrna
  if (is.null(mrna_ids) || length(mrna_ids) == 0) {
    stop("No RefSeq mRNA found for this gene.")
  }

  # Fetch GenBank record for first mRNA
  gb <- entrez_fetch(db = "nuccore", id = mrna_ids[1], rettype = "gb", retmode = "text")
  return(list(gene_id = gene_id, mrna_id = mrna_ids[1], genbank = gb))
}
```

Parse the GenBank text to extract:
- mRNA accession, length
- CDS coordinates (join positions → exon list)
- Full mRNA sequence (ORIGIN section)

**Fallback**: If NCBI fails, query WormBase REST:
```
GET https://wormbase.org/rest/widget/gene/{wbgene_id}/sequences
```
Parse `data.genomic_obj.sequence` for mRNA sequence.

---

### A2. Target Region Screening

```r
screen_targets <- function(mrna_seq, cds_start, cds_end,
                           win_sizes = c(200, 250, 300), step = 25,
                           gc_min = 0.40, gc_max = 0.60,
                           complexity_max = 0.40) {
  candidates <- list()
  mrna_len <- nchar(mrna_seq)

  for (win in win_sizes) {
    starts <- seq(1, mrna_len - win + 1, by = step)
    for (s in starts) {
      subseq <- substr(mrna_seq, s, s + win - 1)
      gc <- (nchar(gsub("[^GCgc]", "", subseq))) / win

      # Low-complexity: fraction of most common dinucleotide
      dinucs <- table(substring(subseq, 1:(win-1), 2:win))
      complexity <- max(dinucs) / sum(dinucs)

      if (gc < gc_min || gc > gc_max) next
      if (complexity > complexity_max) next

      # No internal HindIII (AAGCTT) or SalI (GTCGAC)
      if (grepl("AAGCTT|GTCGAC", toupper(subseq))) next

      # Score
      gc_score  <- 1 - 2 * abs(gc - 0.50)
      cplx_score <- 1 - complexity / complexity_max
      mid <- (s + s + win - 1) / 2
      cds_mid <- (cds_start + cds_end) / 2
      pos_score <- 1 - abs(mid - cds_mid) / (mrna_len / 2)

      score <- 0.4 * gc_score + 0.3 * cplx_score + 0.2 * pos_score + 0.1

      candidates[[length(candidates) + 1]] <- list(
        start = s, end = s + win - 1, length = win,
        gc = round(gc, 4), complexity = round(complexity, 4),
        score = round(score, 4), sequence = subseq
      )
    }
  }

  df <- do.call(rbind, lapply(candidates, as.data.frame))
  df <- df[order(-df$score), ]

  # Remove redundant candidates (overlap > 50%)
  keep <- c()
  for (i in seq_len(nrow(df))) {
    overlaps <- FALSE
    for (j in keep) {
      ov <- min(df$end[i], df$end[j]) - max(df$start[i], df$start[j])
      if (ov > 0.5 * min(df$length[i], df$length[j])) { overlaps <- TRUE; break }
    }
    if (!overlaps) keep <- c(keep, i)
  }
  df[keep, ]
}
```

---

### A3. Primer Design (primer3-py via reticulate)

```r
library(reticulate)
p3 <- import("primer3")

design_primers <- function(target_seq, target_len, relax = FALSE) {
  size_range <- if (relax) list(c(target_len - 30L, target_len + 30L))
                else        list(c(target_len - 15L, target_len + 15L))

  result <- p3$design_primers(
    seq_args = list(
      SEQUENCE_ID        = "target",
      SEQUENCE_TEMPLATE  = target_seq
    ),
    global_args = list(
      PRIMER_OPT_SIZE          = 20L,
      PRIMER_MIN_SIZE          = 18L,
      PRIMER_MAX_SIZE          = 25L,
      PRIMER_OPT_TM            = 60.0,
      PRIMER_MIN_TM            = 57.0,
      PRIMER_MAX_TM            = 63.0,
      PRIMER_MIN_GC            = 40.0,
      PRIMER_MAX_GC            = 60.0,
      PRIMER_PRODUCT_SIZE_RANGE = size_range,
      PRIMER_NUM_RETURN        = 1L,
      PRIMER_THERMODYNAMIC_PARAMETERS_PATH =
        p3$thermoanalysis$DEFAULT_P3_ARGS$PRIMER_THERMODYNAMIC_PARAMETERS_PATH
    )
  )
  result
}

add_re_adapters <- function(fwd, rev) {
  list(
    fwd_with_re = paste0("CCCAAGCTT", fwd),   # HindIII
    rev_with_re = paste0("GGGGTCGAC", rev)    # SalI
  )
}
```

**Thermodynamic QC** (using primer3-py `ThermoAnalysis`):
- Compute hairpin ΔG, homodimer ΔG, heterodimer ΔG for each primer
- Threshold: all ΔG > −9 kcal/mol → PASS
- If any fail, try next Primer3 result or relax size range

---

### A4. Off-target BLAST

```r
library(httr)
library(jsonlite)

blast_offtarget <- function(sequence, taxid = "6239", evalue = "1e-5") {
  # Submit BLAST job
  resp <- POST("https://blast.ncbi.nlm.nih.gov/blast/Blast.cgi",
    body = list(
      CMD = "Put", PROGRAM = "blastn", DATABASE = "nr",
      QUERY = sequence, ENTREZ_QUERY = paste0("txid", taxid, "[Organism]"),
      WORD_SIZE = "11", EXPECT = evalue,
      HITLIST_SIZE = "50", FORMAT_TYPE = "JSON2",
      MEGABLAST = "on"
    ), encode = "form")
  rid <- regmatches(content(resp, "text"),
                    regexpr("(?<=RID = )\\S+", content(resp, "text"), perl = TRUE))

  # Poll for results (max 3 min)
  for (i in 1:18) {
    Sys.sleep(10)
    check <- GET("https://blast.ncbi.nlm.nih.gov/blast/Blast.cgi",
                 query = list(CMD = "Get", RID = rid, FORMAT_TYPE = "JSON2"))
    txt <- content(check, "text")
    if (grepl("Status=READY", txt)) break
  }

  # Parse hits
  result <- fromJSON(txt)
  hits <- result$BlastOutput2$report$results$search$hits
  if (is.null(hits) || length(hits) == 0) return(list(risk = "LOW", hits = NULL))

  # Filter: exclude self-hits (target gene), flag ≥19 bp matches
  off_targets <- Filter(function(h) {
    !grepl(gene_name, h$description[[1]]$title, ignore.case = TRUE) &&
    h$hsps[[1]]$align_len >= 19
  }, hits)

  risk <- if (length(off_targets) == 0) "LOW" else if (length(off_targets) <= 2) "MEDIUM" else "HIGH"
  list(risk = risk, hits = off_targets)
}
```

---

### A5. Output Generation

**CSV** (`{gene}_RNAi_primers.csv`):
Columns: candidate_rank, mrna_start, mrna_end, target_length, gc_pct, score,
exons_covered, fwd_primer, rev_primer, fwd_with_re, rev_with_re,
fwd_tm, rev_tm, fwd_gc, rev_gc, hairpin_dg_fwd, hairpin_dg_rev,
homodimer_dg_fwd, homodimer_dg_rev, heterodimer_dg, product_size,
offtarget_risk, offtarget_genes, recommendation

**SVG** (`{gene}_target_region.svg`) — ggplot2:
- X-axis: mRNA position (nt)
- Exon rectangles (CDS = blue, UTR = light blue), labeled E1–En
- Target region bars above exons (colored by candidate rank)
- Primer arrows for Top candidate
- CDS bracket annotation
- Legend (5 items)
- Figure width: 16 in, height: 6 in

**Chat summary** format:
```
## kynu-1 RNAi Design — Results

**Top candidate (Candidate 1)**
- Target: mRNA nt 704–903 (200 bp, GC = 49.0%)
- Forward: `CCCAAGCTTCCAGTACTATACCGGACAGCT`
- Reverse: `GGGGTCGACCCACCAATTGATCCAGCTCCT`
- Product: 192 bp | Exons: E5+E6 | Off-target risk: LOW

[Alternate candidates table]

**Cloning note**: Digest PCR product with HindIII + SalI, ligate into L4440.
Sequence-verify before feeding to worms.
```

---

## Section B — Evaluate Module

### B0. Input Auto-detection

```r
detect_input_type <- function(input_str) {
  # If two lines / two sequences separated by newline or comma → primer pair
  parts <- strsplit(trimws(input_str), "[\\n,;]+")[[1]]
  parts <- trimws(parts[nzchar(parts)])
  if (length(parts) == 2 && all(nchar(parts) < 40)) {
    return(list(type = "primer_pair", fwd = parts[1], rev = parts[2]))
  }
  # Single long sequence → target region
  seq <- gsub("[^ACGTacgt]", "", paste(parts, collapse = ""))
  if (nchar(seq) >= 50) {
    return(list(type = "target_seq", sequence = toupper(seq)))
  }
  stop("Cannot parse input. Provide a ~200 bp target sequence or two primer sequences.")
}

strip_re_adapters <- function(fwd, rev) {
  # Remove known RE adapter prefixes
  fwd_clean <- sub("^CCCAAGCTT", "", toupper(fwd))
  rev_clean  <- sub("^GGGGTCGAC", "", toupper(rev))
  list(fwd = fwd_clean, rev = rev_clean)
}

infer_target_from_primers <- function(fwd_clean, rev_clean, mrna_seq) {
  # Find fwd primer binding site on mRNA
  fwd_pos <- regexpr(fwd_clean, mrna_seq, ignore.case = TRUE)
  # Reverse complement of rev primer
  rev_rc <- chartr("ACGT", "TGCA", rev_clean)
  rev_rc <- paste(rev(strsplit(rev_rc, "")[[1]]), collapse = "")
  rev_pos <- regexpr(rev_rc, mrna_seq, ignore.case = TRUE)
  if (fwd_pos == -1 || rev_pos == -1) stop("Primers not found in mRNA sequence.")
  start <- as.integer(fwd_pos)
  end   <- as.integer(rev_pos) + nchar(rev_rc) - 1L
  substr(mrna_seq, start, end)
}
```

---

### B1. Dimension 1 — Sequence Specificity (BLAST)

Reuse `blast_offtarget()` from Section A4.
Output: risk level (LOW / MEDIUM / HIGH), list of off-target genes if any.

---

### B2. Dimension 2 — Thermodynamic Stability

```r
evaluate_thermodynamics <- function(target_seq) {
  gc <- (nchar(gsub("[^GCgc]", "", target_seq))) / nchar(target_seq)

  # GC uniformity: sliding window SD
  win <- 20L
  gc_wins <- sapply(seq(1, nchar(target_seq) - win + 1, by = 5), function(i) {
    s <- substr(target_seq, i, i + win - 1)
    nchar(gsub("[^GCgc]", "", s)) / win
  })
  gc_sd <- sd(gc_wins)

  # Estimated dsRNA Tm (Wallace rule approximation for long dsRNA)
  len <- nchar(target_seq)
  n_gc <- round(gc * len)
  n_at <- len - n_gc
  tm_est <- 81.5 + 16.6 * log10(0.05) + 0.41 * (gc * 100) - 675 / len

  list(gc_pct = round(gc * 100, 1), gc_sd = round(gc_sd, 3),
       tm_est = round(tm_est, 1), len = len)
}
```

Score (0–25): GC 45–55% → 25; 40–45% or 55–60% → 15; else → 5.
GC uniformity bonus: gc_sd < 0.08 → +5 (reported separately).

---

### B3. Dimension 3 — RNAi Efficiency Rules

```r
score_efficiency_rules <- function(target_seq, mrna_seq, cds_start, cds_end) {
  len  <- nchar(target_seq)
  gc   <- nchar(gsub("[^GCgc]", "", target_seq)) / len
  pos  <- regexpr(target_seq, mrna_seq, ignore.case = TRUE)
  mid  <- pos + len / 2

  # Dinucleotide complexity
  dinucs <- table(substring(target_seq, 1:(len-1), 2:len))
  complexity <- max(dinucs) / sum(dinucs)

  scores <- c(
    gc_content   = if (gc >= 0.45 && gc <= 0.55) 25 else if (gc >= 0.40 && gc <= 0.60) 15 else 5,
    cds_position = if (mid > cds_start + 0.2*(cds_end-cds_start) &&
                       mid < cds_start + 0.8*(cds_end-cds_start)) 20 else 10,
    low_repeat   = if (complexity < 0.30) 20 else if (complexity < 0.40) 10 else 0,
    length_ok    = if (len >= 200 && len <= 300) 20 else if (len >= 150) 10 else 5,
    no_re_sites  = if (!grepl("AAGCTT|GTCGAC", toupper(target_seq))) 15 else 0
  )
  list(total = sum(scores), breakdown = scores)
}
```

Total out of 100. Interpretation: ≥ 80 → Excellent, 60–79 → Good, 40–59 → Acceptable, < 40 → Poor.

---

### B4. Dimension 4 — WormBase Existing Clone Query

```r
query_wormbase_rnai <- function(gene_name) {
  # Try WormBase REST API
  url <- paste0("https://wormbase.org/search/gene/", gene_name, "?species=c_elegans")
  resp <- tryCatch(GET(url, timeout(10)), error = function(e) NULL)
  if (is.null(resp) || status_code(resp) != 200) {
    return(list(available = FALSE,
                note = "WormBase API unavailable. Check manually at https://wormbase.org/"))
  }

  # Parse gene ID then query RNAi widget
  gene_id <- extract_wbgene_id(content(resp, "text"))  # regex for WBGene\d+
  rnai_url <- paste0("https://wormbase.org/rest/widget/gene/", gene_id, "/rnai")
  rnai_resp <- tryCatch(GET(rnai_url, timeout(15)), error = function(e) NULL)
  if (is.null(rnai_resp) || status_code(rnai_resp) != 200) {
    return(list(available = FALSE,
                note = "RNAi widget unavailable. Check manually at WormBase."))
  }

  data <- fromJSON(content(rnai_resp, "text"))
  clones <- data$data$rnai  # list of RNAi experiments
  n_clones <- if (is.null(clones)) 0 else length(clones)

  list(available = TRUE, n_existing_clones = n_clones,
       note = if (n_clones > 0)
         paste0(n_clones, " existing RNAi experiment(s) found on WormBase for this gene.")
       else
         "No existing RNAi clones found on WormBase.")
}
```

If `n_existing_clones > 0`, report as "Existing validated clones available — consider ordering from Ahringer/Vidal library before cloning de novo."

---

### B5. Evaluation Output

**CSV** (`{gene}_RNAi_evaluation.csv`):
Columns: gene, input_type, target_length, gc_pct, gc_sd, tm_est,
blast_risk, blast_offtargets, efficiency_score, efficiency_gc,
efficiency_position, efficiency_repeat, efficiency_length, efficiency_re,
wormbase_clones, overall_recommendation

**Chat report** format:
```
## daf-2 RNAi Sequence Evaluation

| Dimension | Score / Result | Status |
|-----------|---------------|--------|
| Sequence specificity (BLAST) | LOW risk, 0 off-targets | ✅ |
| Thermodynamic stability | GC=48%, Tm≈82°C, GC-SD=0.06 | ✅ |
| Efficiency rules | 85/100 (Excellent) | ✅ |
| WormBase existing clones | 12 existing experiments | ℹ️ |

**Overall: ✅ Recommended — sequence is suitable for L4440 cloning.**
```

Overall recommendation logic:
- ✅ Recommended: BLAST LOW + efficiency ≥ 60
- ⚠️ Use with caution: BLAST MEDIUM **or** efficiency 40–59
- ❌ Redesign recommended: BLAST HIGH **or** efficiency < 40

---

## Section C — Error Handling

| Situation | Action |
|-----------|--------|
| NCBI gene not found | Ask user to confirm spelling; try WormBase API fallback |
| No candidates pass GC filter | Relax to 35–65%, re-screen, annotate in output |
| Primer3 design fails | Relax product size ±30 bp and retry once |
| BLAST timeout (>3 min) | Mark off-target as "NOT CHECKED — verify manually"; continue |
| Primer not found in mRNA (evaluate) | Ask user to provide target region sequence directly |
| WormBase API unavailable | Skip dimension 4, note in report, do not fail |
| Gene has multiple mRNA isoforms | Use longest RefSeq NM_ record; note isoform in output |

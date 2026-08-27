"""Structure literature evidence for top candidates (disease-agnostic).

This module does NOT call any tool directly. The AGENT runs the Biomni
`LiteratureSearch` tool (which writes structured records to
/mnt/results/execution_trace/references.jsonl) once per candidate, then passes the
returned records here to build a tidy evidence table for the report.

Rationale for agent-driven search: LiteratureSearch is a first-class Biomni tool and
must be invoked by the agent, not shelled out. Keeping formatting here keeps the
pipeline reproducible while the agent supplies grounded, citeable results.

Recommended workflow (agent side):
  1. Take the candidate slate from the annotated frame with `candidate_slate(...)`. This is
     the top-K approved reversers (default K=10) UNION the canonical #1 hit (via
     `ensure_ranks`), so the overall top-ranked drug is ALWAYS rationalized -- even if it is
     non-approved or a likely artifact. Use `top_hit_row(...)` to get the #1 hit explicitly.
  2. For each, build a query with `build_query(drug, disease, moa)`.
  3. Run LiteratureSearch(query, max_papers=5) -- optionally human=True.
  4. Collect the one-line summaries + the structured records in references.jsonl. Turn those
     records into the report's reference list with `references_from_records(records)` -- this
     formats authors, title, journal, year and ALWAYS appends a verifiable locator (PMID /
     DOI / URL), so bibliographic detail comes from the retrieved record, not the model's
     memory.
  5. Call `assemble_evidence_table(rows)` where each row is a dict you fill in from
     the search (drug, moa, direction, evidence summary, ref indices, clinical_status).
  6. Supply a mandatory `top_hit_rationale` string to the report for the canonical #1 hit
     (an honest "likely non-specific / assay artifact" call is a valid rationale).

Public API:
  build_query(drug, disease, moa=None, mode="therapeutic") -> str
  top_hit_row(annotated_df) -> dict                              # the canonical #1 to rationalize
  candidate_slate(annotated_df, k=10, approved_only=True, ensure_ranks=(1,)) -> DataFrame
  assemble_evidence_table(rows) -> DataFrame
  load_reference_records(path) -> list[dict]   # parse references.jsonl if needed
  references_from_records(records, drugs=None) -> list[str]  # formatted refs w/ locator
"""
import json
import re
import pandas as pd


def build_query(drug, disease, moa=None, mode="therapeutic"):
    """Construct a focused literature query for a drug-disease pair.

    mode="therapeutic": efficacy/treatment evidence (for reversers).
    mode="safety": tolerability/toxicity signal.
    mode="mechanism": MOA-to-disease-pathway linkage.
    """
    drug = str(drug).strip()
    disease = str(disease).strip()
    if mode == "safety":
        return f"{drug} safety tolerability adverse effects in {disease} or related conditions"
    if mode == "mechanism" and moa:
        return f"{moa} mechanism in {disease} pathogenesis and treatment ({drug})"
    # therapeutic (default)
    base = f"{drug} treatment of {disease}"
    if moa:
        base += f" ({moa})"
    return base + " efficacy clinical or preclinical evidence"


def _rank_col(df):
    """Prefer the authoritative integer canonical_rank; fall back to consensus_rank."""
    return "canonical_rank" if "canonical_rank" in df.columns else "consensus_rank"


def top_hit_row(annotated_df):
    """Return the single canonical #1 row (as a dict) that MUST be rationalized.

    This is the overall top-ranked hit by canonical_rank -- it may be non-approved or even a
    likely assay artifact, but it must never be emitted unexplained. Used to force the top hit
    into the literature slate and to require a rationale in the report.
    """
    rc = _rank_col(annotated_df)
    top = annotated_df.sort_values(rc).iloc[0]
    keep = [c for c in ["canonical_rank", "consensus_rank", "drug", "pert", "moa", "target",
                        "clinical_phase", "approved", "organism", "S_reversal", "fdr_reversal",
                        "reversal_enrich"] if c in annotated_df.columns]
    return {c: top[c] for c in keep}


def candidate_slate(annotated_df, k=10, approved_only=True, ensure_ranks=(1,)):
    """Return the reversers to literature-check, ordered by the canonical ranking.

    Selection = the top-k reversers (preferring approved/Launched drugs, falling back to
    any-phase if too few) UNION any rows whose canonical_rank is in `ensure_ranks`
    (default: the canonical #1). The union guarantees the overall top-ranked hit is always
    rationalized even if it is not approved -- fixing the "top hit emitted unexplained" bug.
    The returned slate is itself ordered by canonical_rank and carries an `is_canonical_top`
    flag marking the rows that were force-included.
    """
    rc = _rank_col(annotated_df)
    pos = annotated_df[annotated_df["S_reversal"] > 0].copy()
    # top-k slate (approved-preferred)
    sel = pos.copy()
    if approved_only and sel["approved"].sum() >= k:
        sel = sel[sel["approved"]]
    sel = sel.sort_values(rc).head(k)
    # force-include the ensured canonical ranks (from the FULL frame, not just positives/approved)
    ensure_ranks = set(ensure_ranks or [])
    if ensure_ranks and rc in annotated_df.columns:
        forced = annotated_df[annotated_df[rc].isin(ensure_ranks)]
        slate = pd.concat([sel, forced]).drop_duplicates(subset=[rc]).sort_values(rc)
    else:
        slate = sel.sort_values(rc)
    slate = slate.reset_index(drop=True)
    slate["is_canonical_top"] = slate[rc].isin(ensure_ranks) if rc in slate.columns else False
    cols = [c for c in [rc, "drug", "pert", "moa", "target", "clinical_phase", "approved",
                        "S_reversal", "fdr_reversal", "reversal_enrich", "consensus_rank",
                        "is_canonical_top"] if c in slate.columns]
    return slate[cols]


def assemble_evidence_table(rows):
    """Build the literature evidence table from agent-collected rows.

    Each row: dict(drug, moa, direction, evidence, refs, clinical_status).
      - evidence: 1-2 sentence grounded synthesis from the search results.
      - refs: comma-joined citation indices (matching the report's reference list).
      - clinical_status: honest note on trial stage for THIS indication.
    """
    df = pd.DataFrame(rows)
    expected = ["drug", "moa", "direction", "evidence", "refs", "clinical_status"]
    for c in expected:
        if c not in df.columns:
            df[c] = ""
    return df[expected]


def load_reference_records(path="/mnt/results/execution_trace/references.jsonl"):
    """Parse the references.jsonl written by LiteratureSearch into a list of dicts."""
    recs = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return recs


def _format_authors(authors):
    """Format an authors field (list or string) into a compact citation string."""
    if not authors:
        return ""
    if isinstance(authors, list):
        names = [str(a) for a in authors if a]
        if not names:
            return ""
        if len(names) <= 3:
            return ", ".join(names)
        return f"{names[0]} et al."
    s = str(authors).strip()
    if not s:
        return ""
    # If it's a semicolon or comma separated string, truncate
    parts = [p.strip() for p in re.split(r"[;]", s) if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) <= 3:
        return ", ".join(parts)
    return f"{parts[0]} et al."


def _extract_locator(rec):
    """Extract a verifiable locator from a LiteratureSearch record.

    Priority: PMID > DOI > URL. Returns a string like 'PMID: 12345' / 'doi:10.1038/...'
    / the URL, or None if no locator is present.
    """
    # PMID: check 'pmid' key, then 'citation_id' if it looks numeric
    pmid = rec.get("pmid")
    if pmid:
        return f"PMID: {pmid}"
    cid = rec.get("citation_id")
    if cid and str(cid).strip().isdigit():
        return f"PMID: {cid}"
    # DOI
    doi = rec.get("doi")
    if doi:
        return f"doi:{doi}"
    # URL
    url = rec.get("url")
    if url:
        return str(url)
    return None


def references_from_records(records, drugs=None):
    """Format numbered reference strings directly from LiteratureSearch records.

    Each record (from /mnt/results/execution_trace/references.jsonl) has fields like
    index, citation_id, title, authors, year, journal, doi, url, study_type,
    citation_count, abstract. This function turns them into the report's reference list:
    'Authors. Title. Journal, Year. PMID: <pmid>' (or doi:/URL).

    ALWAYS appends the locator found on the record; SKIPS records with no locator (a
    reference without a PMID/DOI/URL cannot be verified by a reviewer). Bibliographic
    detail comes from the retrieved record instead of the model's memory.

    records : list of dicts (from load_reference_records or the LiteratureSearch return)
    drugs   : optional list of drug names; if given, only records whose title/abstract
              mention one of the drugs are included (helps filter to relevant refs).

    Returns list[str] of formatted reference strings (numbered [1], [2], ... by position).
    """
    out = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        # Optional drug filter
        if drugs:
            title = str(rec.get("title", "")).lower()
            abstract = str(rec.get("abstract", "")).lower()
            if not any(d.lower() in title or d.lower() in abstract for d in drugs):
                continue

        locator = _extract_locator(rec)
        if not locator:
            continue  # skip records with no verifiable locator

        authors = _format_authors(rec.get("authors"))
        title = str(rec.get("title", "")).strip()
        journal = str(rec.get("journal", "")).strip()
        year = rec.get("year", "")

        parts = []
        if authors:
            parts.append(authors + ".")
        if title:
            parts.append(title + ".")
        if journal:
            jy = journal
            if year:
                jy += f", {year}"
            parts.append(jy + ".")
        parts.append(locator)
        out.append(" ".join(parts))
    return out

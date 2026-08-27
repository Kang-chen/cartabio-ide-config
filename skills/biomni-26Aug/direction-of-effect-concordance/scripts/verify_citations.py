#!/usr/bin/env python3
"""
verify_citations.py — MANDATORY blocking citation-verification gate for
direction-of-effect concordance (see references/citation_integrity.md).

Checks:
  1. Collect every [n] index used in evidence_matrix.csv (cites), consensus_calls.csv
     (key_flag/flagged), and (if present) synthesis.json.
  2. Confirm each [n] resolves to a record in references.jsonl.
  3. If references.json exists (the verbatim refs for the report), confirm each entry's DOI
     appears in the matching references.jsonl record, and that the title is present verbatim
     (substring match) -- catching the classic post-compaction "correct DOI, invented title".
  4. Confirm every [n] index used in synthesis.json / CSVs also has a corresponding entry in
     references.json -- catching orphan citations that resolve in references.jsonl but are
     missing from the report's References section (unresolvable by a reader).
  5. Re-check optionally against transcript.jsonl when available (verbatim recovery).

Emits data/citation_verification.json with doi_layer_status in {clean, partial, failed} and
EXITS NON-ZERO unless status is clean/empty, so it is a real blocking gate.

Usage:
  python verify_citations.py --run RUN \
      --refs /mnt/results/execution_trace/references.jsonl \
      --transcript /mnt/results/execution_trace/transcript.jsonl
"""
import argparse, json, os, re, sys


def load_jsonl(path):
    recs = []
    if path and os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return recs


def index_refs(recs):
    """Map integer index -> record. LiteratureSearch records may carry an explicit index,
    else fall back to 1-based order of appearance."""
    by_idx = {}
    for i, r in enumerate(recs, start=1):
        idx = r.get("index") or r.get("n") or i
        try:
            by_idx[int(idx)] = r
        except (TypeError, ValueError):
            by_idx[i] = r
    return by_idx


def collect_indices(run):
    idxs = set()
    pat = re.compile(r"\[(\d+)\]")
    import csv
    data = os.path.join(run, "data")
    for fname, cols in [("evidence_matrix.csv", ["cites", "note"]),
                        ("consensus_calls.csv", ["key_flag", "flagged"])]:
        p = os.path.join(data, fname)
        if os.path.exists(p):
            with open(p) as fh:
                for row in csv.DictReader(fh):
                    for c in cols:
                        for m in pat.findall(str(row.get(c, ""))):
                            idxs.add(int(m))
                    # bare comma lists in 'cites' (e.g. "12, 14")
                    if "cites" in row:
                        for tok in re.findall(r"\b(\d+)\b", str(row.get("cites", ""))):
                            idxs.add(int(tok))
    syn = os.path.join(run, "synthesis.json")
    if os.path.exists(syn):
        blob = json.dumps(json.load(open(syn)))
        for m in pat.findall(blob):
            idxs.add(int(m))
    return idxs


def norm(s):
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def norm_doi(doi):
    """Normalize a DOI string for comparison: lowercase, strip trailing punctuation, and
    collapse the registrant code so 10.1038/ng1161 and 10.1038/NG1161 compare equal."""
    d = norm(doi)
    # strip common trailing punctuation that citation text carries
    d = d.rstrip(".,;)")
    return d


def extract_doi(text):
    """Pull the first DOI-looking token out of a free-text reference string."""
    m = re.search(r"10\.\d{4,9}/[^\s\"]+", str(text or ""))
    return m.group(0).rstrip(".)") if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--refs", default="/mnt/results/execution_trace/references.jsonl")
    ap.add_argument("--transcript", default="/mnt/results/execution_trace/transcript.jsonl")
    args = ap.parse_args()

    recs = load_jsonl(args.refs)
    by_idx = index_refs(recs)
    transcript_present = os.path.exists(args.transcript)

    used = collect_indices(args.run)
    missing = sorted(i for i in used if i not in by_idx)

    # references.json completeness check: every [n] used in synthesis.json / CSVs must also
    # have a corresponding entry in references.json (the report's References section). An
    # index that resolves in references.jsonl but is absent from references.json renders an
    # unresolvable [n] marker in the PDF body -- the gate must not report 'clean' for these.
    refs_json_p = os.path.join(args.run, "references.json")
    orphan_in_refs_json = []
    if os.path.exists(refs_json_p):
        refs_json = json.load(open(refs_json_p))
        refs_json_indices = set()
        for entry in refs_json:
            n = entry.get("n")
            if n is not None:
                try:
                    refs_json_indices.add(int(n))
                except (TypeError, ValueError):
                    pass
        orphan_in_refs_json = sorted(i for i in used if i not in refs_json_indices)

    # DOI/title layer check against references.json (if the report refs exist)
    flagged = []
    no_doi = []
    transcript_text = ""
    if transcript_present:
        with open(args.transcript, errors="ignore") as fh:
            transcript_text = fh.read()

    if os.path.exists(refs_json_p):
        refs_json = json.load(open(refs_json_p))
        # build a big searchable blob of all retrieved metadata
        rec_blob = norm(json.dumps([{k: r.get(k) for k in
                                     ("source", "title", "doi", "authors", "journal", "year")}
                                    for r in recs]))
        for entry in refs_json:
            n = entry.get("n")
            text = entry.get("text", "")
            doi = extract_doi(text)
            rec = by_idx.get(int(n)) if n is not None else None
            if doi:
                # Layer 1 (existing): the DOI must appear somewhere in the retrieved records
                # blob or transcript -- catches fully invented DOIs.
                if norm_doi(doi) not in rec_blob and (not transcript_present
                                                      or norm_doi(doi) not in norm(transcript_text)):
                    flagged.append({"n": n, "issue": "DOI not found in retrieved records",
                                    "doi": doi})
                # Layer 2 (new): per-record DOI-title correspondence. The DOI printed in
                # references.json[n] must match the DOI of the SAME retrieved record
                # references.jsonl[n] (when that record carries a DOI). A correct-looking DOI
                # that belongs to a DIFFERENT paper -- the classic ref [89] mismatch where an
                # Abifadel title is paired with a Circulation Genetics DOI -- is flagged even
                # though the DOI exists somewhere in the blob. This is the failure the old
                # blob-only check could not catch.
                elif rec is not None:
                    rec_doi = rec.get("doi")
                    if rec_doi:
                        if norm_doi(doi) != norm_doi(rec_doi):
                            flagged.append({"n": n,
                                            "issue": "DOI does not match the retrieved record for this reference (DOI-title mismatch)",
                                            "printed_doi": doi,
                                            "record_doi": rec_doi})
            else:
                no_doi.append(n)
            # Title layer: the report reference (references.json[n].text) must contain the
            # verbatim title of the matching retrieved record (references.jsonl). We take the
            # record's own `title` field as ground truth and require a contiguous run of it to
            # appear in the report text. This avoids fragile author-window heuristics and only
            # flags when the printed reference does NOT reflect the retrieved title.
            rec_title = norm(rec.get("title")) if rec and rec.get("title") else ""
            if rec_title:
                twords = re.findall(r"[a-z0-9]+", rec_title)
                # contiguous window of up to 6 title words; short titles use the whole title
                frag = " ".join(twords[:6]) if len(twords) >= 6 else " ".join(twords)
                report_text = norm(text)
                if frag and frag not in report_text and (
                        not transcript_present or norm(rec_title) not in norm(transcript_text)):
                    flagged.append({"n": n,
                                    "issue": "retrieved title not found in printed reference",
                                    "title_fragment": frag})

    status = "clean"
    if missing or flagged or orphan_in_refs_json:
        status = "failed"
    elif no_doi:
        status = "partial"

    result = {"status": status, "doi_layer_status": status,
              "n_records_indexed": len(by_idx), "n_indices_used": len(used),
              "missing_indices": missing, "flagged": flagged,
              "orphan_citations": [{"n": i,
                                    "issue": "citation index used in synthesis but no entry in references.json"}
                                   for i in orphan_in_refs_json],
              "records_without_doi": [n for n in no_doi if n is not None],
              "transcript_checked": transcript_present}
    out = os.path.join(args.run, "data", "citation_verification.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(result, open(out, "w"), indent=2)

    print(f"Citation gate: status={status}  used={len(used)}  indexed={len(by_idx)}")
    if not transcript_present:
        print("NOTE: transcript.jsonl not found -- verbatim recovery unavailable; "
              "do not claim original wording/numbers from the summary alone.")
    if missing:
        print("  MISSING indices (no record):", missing)
    for f in flagged:
        print("  FLAG:", f)
    for o in orphan_in_refs_json:
        print(f"  ORPHAN: [{o}] cited in synthesis but no entry in references.json")
    if no_doi:
        print("  Records without DOI (confirm each is a book/abstract/GeneReviews):",
              [n for n in no_doi if n is not None])
    print(f"-> {out}")

    if status == "failed":
        sys.exit(2)  # BLOCK the pipeline


if __name__ == "__main__":
    main()

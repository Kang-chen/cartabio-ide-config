#!/usr/bin/env python3
"""
verify_citations.py -- BLOCKING citation-verification gate.

Checks every DOI and every title used in the populated artifacts against the retrieved
records (corpus.csv + references.jsonl) and, when present, the verbatim transcript
(transcript.jsonl). Emits citation_verification.json with doi_layer_status.

What it catches automatically:
  * A DOI cited in an artifact that does NOT appear in any retrieved record / transcript
    (unverifiable citation) -> flagged.
  * A title whose DOI is known but whose title text does not match the record's title
    (possible paraphrase/fabrication) -> flagged as title_mismatch.

What it CANNOT fully automate:
  * Whether a specific NUMBER is faithful to its source. The script extracts numeric tokens
    from `finding`/`key_metric`/`effect_or_metric` fields and checks whether each appears in
    the source record's abstract OR the transcript; anything not found is listed for MANUAL
    confirmation (read the full text). This is a safety net, not a substitute for judgment.

Usage:
  python verify_citations.py --run RUN_DIR \
      --refs /mnt/results/execution_trace/references.jsonl \
      --transcript /mnt/results/execution_trace/transcript.jsonl
"""
import argparse, csv, json, os, re, sys

PREPRINT = ("arxiv", "biorxiv", "medrxiv", "10.48550", "10.1101")


def norm_doi(d):
    if not d:
        return ""
    d = str(d).strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", d).strip()


def norm_title(t):
    return re.sub(r"[^a-z0-9 ]", "", str(t or "").lower()).strip()


# Short-form citation patterns like "Schurch et al. 2016", "Su/SEQC Consortium 2014",
# "Love & Huber 2014". These are legitimate citation labels, NOT full titles, so the
# title-vs-record check must skip them (only the DOI-presence check applies).
_SHORT_CIT = re.compile(
    r"^\s*[A-Z][\w.\-/&' ]{0,40}?"
    r"(et al\.?|consortium|and colleagues|&[\w ]+)?[,]?\s*\(?(19|20)\d{2}\)?\s*$",
    re.IGNORECASE,
)


def _looks_like_full_title(s):
    """True only if the field looks like a real full paper title (worth title-checking)."""
    s = (s or "").strip()
    if not s:
        return False
    if _SHORT_CIT.match(s):
        return False
    # Full titles are typically long, multi-word phrases.
    return len(s) > 25 and len(s.split()) >= 5


def load_corpus(path):
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            d = norm_doi(r.get("doi"))
            if d:
                out[d] = r
    return out


def load_refs(path):
    """references.jsonl -> {doi: title}."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = norm_doi(r.get("doi"))
            if d:
                out[d] = r.get("title", "")
    return out


def load_transcript_text(path):
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, errors="ignore") as f:
            return f.read().lower()
    except OSError:
        return ""


def collect_artifact_items(run_dir):
    """Return (citations, number_claims) from populated artifacts."""
    citations = []   # {doi, title, where}
    numbers = []     # {doi, text, where}

    def add_json(name, doi_key, title_key, num_keys):
        p = os.path.join(run_dir, name)
        if not os.path.exists(p):
            return
        try:
            data = json.load(open(p))
        except (json.JSONDecodeError, OSError):
            return
        for row in (data if isinstance(data, list) else [data]):
            doi = norm_doi(row.get(doi_key))
            title = row.get(title_key, "") if title_key else ""
            if doi:
                citations.append({"doi": doi, "title": title, "where": name})
            for nk in num_keys:
                val = str(row.get(nk, ""))
                if re.search(r"\d", val):
                    numbers.append({"doi": doi, "text": val, "where": f"{name}:{nk}"})

    # comparison mode
    add_json("benchmark_catalog.json", "doi", "defining_paper",
             ["key_metric", "truth_basis"])
    add_json("performance_claims.json", "doi", "source", ["finding"])
    # topic mode (evidence_table is CSV)
    ev = os.path.join(run_dir, "evidence_table.csv")
    if os.path.exists(ev):
        with open(ev) as f:
            for row in csv.DictReader(f):
                doi = norm_doi(row.get("doi"))
                if doi:
                    citations.append({"doi": doi, "title": row.get("source", ""),
                                      "where": "evidence_table.csv"})
                for nk in ("finding", "effect_or_metric"):
                    val = str(row.get(nk, ""))
                    if re.search(r"\d", val):
                        numbers.append({"doi": doi, "text": val,
                                        "where": f"evidence_table.csv:{nk}"})
    return citations, numbers


def _num_tokens(text):
    # meaningful numeric tokens (skip lone small integers that are noise)
    toks = re.findall(r"\d+\.?\d*%?", text)
    return [t for t in toks if not (t.isdigit() and len(t) <= 1)]


def verify(run_dir, refs_path, transcript_path):
    corpus = load_corpus(os.path.join(run_dir, "corpus.csv"))
    refs = load_refs(refs_path)
    tx = load_transcript_text(transcript_path)
    known_titles = {}
    known_titles.update({d: corpus[d].get("title", "") for d in corpus})
    for d, t in refs.items():
        known_titles.setdefault(d, t)

    citations, numbers = collect_artifact_items(run_dir)
    flagged = []

    # --- citation checks ---
    cit_ok = 0
    for c in citations:
        d = c["doi"]
        in_record = d in known_titles
        in_tx = bool(tx) and d in tx
        if not (in_record or in_tx):
            flagged.append({"item": f"DOI {d} ({c['where']})",
                            "reason": "doi_not_in_records_or_transcript"})
            continue
        # Title check applies ONLY to full-title fields, not short-form citations.
        # Short citations like "Schurch et al. 2016" are legitimate and must not be
        # title-checked against the record's full title.
        rec_title = norm_title(known_titles.get(d, ""))
        art_title = norm_title(c["title"])
        if rec_title and _looks_like_full_title(c["title"]):
            if art_title not in rec_title and rec_title not in art_title:
                # possible paraphrase/fabrication of the title slot
                flagged.append({"item": f"title vs DOI {d} ({c['where']})",
                                "reason": "title_mismatch"})
                continue
        cit_ok += 1

    # --- number checks (safety net) ---
    num_ok, num_manual = 0, []
    for n in numbers:
        toks = _num_tokens(n["text"])
        if not toks:
            num_ok += 1
            continue
        d = n["doi"]
        abstract = norm_title(corpus.get(d, {}).get("abstract", "")) if d in corpus else ""
        hay = abstract + " " + tx
        missing = [t for t in toks if t.lower().rstrip("%") not in hay]
        if missing:
            num_manual.append({"item": f"numbers {missing} in {n['where']}",
                               "reason": "number_not_found_in_abstract_or_transcript"})
        else:
            num_ok += 1

    n_cit, n_num = len(citations), len(numbers)
    hard_fail = [f for f in flagged]  # citation problems are hard
    if not citations and not numbers:
        status = "empty"
    elif hard_fail:
        status = "failed" if len(hard_fail) > max(1, 0.25 * max(1, n_cit)) else "partial"
    elif num_manual:
        status = "partial"
    else:
        status = "clean"

    result = {
        "doi_layer_status": status,
        "citations_checked": n_cit,
        "citations_verified": cit_ok,
        "numbers_checked": n_num,
        "numbers_verified": num_ok,
        "flagged_or_dropped": flagged + num_manual,
        "provenance_note": ("verified against corpus.csv + references.jsonl"
                            + ("" if not tx else " + transcript.jsonl re-check")),
        "known_caveats": [
            "Ordinal scorecards are qualitative syntheses, not re-measured metrics.",
            "Number safety-net checks string presence, not semantic faithfulness; "
            "confirm flagged numbers against full text.",
        ],
    }
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run directory with populated artifacts")
    ap.add_argument("--refs", default="/mnt/results/execution_trace/references.jsonl")
    ap.add_argument("--transcript",
                    default="/mnt/results/execution_trace/transcript.jsonl")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    res = verify(args.run, args.refs, args.transcript)
    out = args.out or os.path.join(args.run, "citation_verification.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=2)
    print(f"doi_layer_status: {res['doi_layer_status']}")
    print(f"  citations: {res['citations_verified']}/{res['citations_checked']} verified")
    print(f"  numbers:   {res['numbers_verified']}/{res['numbers_checked']} verified")
    if res["flagged_or_dropped"]:
        print(f"  FLAGGED ({len(res['flagged_or_dropped'])}):")
        for x in res["flagged_or_dropped"][:12]:
            print(f"    - {x['item']}: {x['reason']}")
    print(f"  -> {out}")
    # non-zero exit on failure so the gate can block a pipeline
    return 0 if res["doi_layer_status"] in ("clean", "empty") else 1


if __name__ == "__main__":
    sys.exit(main())

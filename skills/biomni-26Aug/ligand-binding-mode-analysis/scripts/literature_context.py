"""
Ground the binding-pocket analysis in the primary literature.

This module is a thin, DEFENSIVE wrapper describing how to obtain real, cited
references for the target-ligand pair. In a Biomni agent session, the agent
should call the platform `LiteratureSearch` tool (which writes structured records
to /mnt/results/execution_trace/references.jsonl) and pass the returned findings
into build_report via `references=[...]`.

Rules baked in here to prevent fabrication:
  - NEVER invent citations, PDB codes, or numeric findings.
  - Only pass references that came back from a real search or are supplied by the
    user. If none are available, the report clearly states that the pocket was not
    cross-referenced with the literature.

The `format_references` helper normalizes reference dicts into the shape the PDF
builder expects, and `suggested_queries` builds sensible search strings.
"""


def suggested_queries(target_name, ligand_name=None, is_kinase=False):
    """Return a list of good LiteratureSearch query strings for this pocket."""
    q = []
    if ligand_name and target_name:
        q.append(f"{ligand_name} {target_name} co-crystal structure binding mode")
        q.append(f"{ligand_name} {target_name} interactions hydrogen bonds")
    if target_name:
        q.append(f"{target_name} inhibitor binding site key residues")
    if is_kinase:
        q.append(f"{target_name} DFG conformation gatekeeper hinge inhibitor")
    return q


def format_references(raw_refs):
    """
    Normalize a list of reference dicts into a consistent schema.

    Accepts flexible input keys (title/authors/journal/year/doi/pmid/url) and
    returns a list of dicts with those keys, dropping empties. This is what
    build_report expects for the References section.
    """
    out = []
    for r in raw_refs or []:
        if not isinstance(r, dict):
            # allow plain strings
            out.append({"citation": str(r)})
            continue
        entry = {
            "title": r.get("title") or r.get("name"),
            "authors": r.get("authors") or r.get("author"),
            "journal": r.get("journal") or r.get("venue"),
            "year": r.get("year") or r.get("published_year"),
            "doi": r.get("doi"),
            "pmid": r.get("pmid") or r.get("pubmed_id"),
            "url": r.get("url"),
        }
        entry = {k: v for k, v in entry.items() if v}
        if entry:
            out.append(entry)
    return out


def reference_to_citation(entry):
    """Render one normalized reference dict to a readable citation string."""
    if "citation" in entry:
        return entry["citation"]
    bits = []
    if entry.get("authors"):
        bits.append(str(entry["authors"]))
    if entry.get("year"):
        bits.append(f"({entry['year']})")
    if entry.get("title"):
        bits.append(entry["title"].rstrip(".") + ".")
    if entry.get("journal"):
        bits.append(f"<i>{entry['journal']}</i>.")
    if entry.get("doi"):
        bits.append(f"doi:{entry['doi']}")
    elif entry.get("pmid"):
        bits.append(f"PMID:{entry['pmid']}")
    elif entry.get("url"):
        bits.append(entry["url"])
    return " ".join(bits)

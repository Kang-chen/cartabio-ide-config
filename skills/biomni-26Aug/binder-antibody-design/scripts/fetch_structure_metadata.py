#!/usr/bin/env python3
"""
fetch_structure_metadata.py -- Fetch structural metadata for a PDB entry from the
RCSB Data REST API and write a provenance-stamped JSON sidecar for build_report.py.

Why this exists: facts about an external database (a structure's resolution, the
experimental method used to solve it, its deposition date, its chain composition)
must be READ FROM that database at run time, never hand-typed into a report config.
A hand-typed resolution is a hallucination waiting to happen -- nothing downstream
can catch "2.10 A" when the real value is 0.99 A. This script is the single point
where those facts enter the workflow, and every value it emits carries provenance
(the source URL and the fetch timestamp) so build_report.py can gate on it.

It is deliberately dependency-free (Python standard library only) so it never blocks
the pipeline on an extra install.

The report NEVER falls back to a typed default: if the fetch fails, this script still
writes a sidecar, but with `available: false`, all fact fields null, and the error
recorded in `provenance.error`. build_report.py then prints "unavailable" for those
fields rather than inventing a number.

Example:
    python fetch_structure_metadata.py --pdb 5O45 --out structure_metadata.json

Output sidecar (success):
    {
      "pdb_id": "5O45",
      "available": true,
      "resolution_A": 0.99,
      "experimental_method": "X-ray",
      "deposition_date": "2017-05-26",
      "release_date": "2017-09-20",
      "title": "Structure of human PD-L1 in complex with inhibitor",
      "polymer_composition": "heteromeric protein",
      "polymer_entity_instance_count": 2,
      "provenance": {
        "pdb_id": "5O45",
        "source_url": "https://data.rcsb.org/rest/v1/core/entry/5O45",
        "fetched_at": "2026-08-06T19:15:00Z",
        "api": "RCSB Data REST API v1"
      }
    }

EXIT CODES
  0 success (structure metadata fetched and written);
  2 usage error;
  3 fetch/parse failure (an `available: false` sidecar is still written so the
    workflow can continue and the report can honestly say "unavailable").
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

# RCSB Data REST API -- the canonical machine-readable source for PDB entry metadata.
RCSB_ENTRY_BASE = "https://data.rcsb.org/rest/v1/core/entry"
API_LABEL = "RCSB Data REST API v1"

# The structural properties this workflow may surface in a report. Each is fetched;
# none may be hand-supplied. build_report.py cross-checks displayed values against
# these keys, so keep this list and the sidecar schema in sync.
FACT_FIELDS = (
    "resolution_A",
    "experimental_method",
    "deposition_date",
    "release_date",
    "title",
    "polymer_composition",
    "polymer_entity_instance_count",
)


def eprint(*a):
    print(*a, file=sys.stderr)


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_url(pdb_id):
    return f"{RCSB_ENTRY_BASE}/{pdb_id.upper()}"


def _clean_date(raw):
    """RCSB dates look like '2017-05-26T00:00:00.000+00:00'. Keep the calendar date."""
    if not raw or not isinstance(raw, str):
        return None
    return raw.split("T", 1)[0]


def parse_entry_json(pdb_id, data, source_url, fetched_at=None):
    """Build a provenance-stamped metadata record from a raw RCSB entry JSON dict.

    Pure function (no network) so it can be unit-tested against a saved fixture.
    Missing individual fields become None -- they are reported as "unavailable"
    downstream, never guessed.
    """
    fetched_at = fetched_at or _utc_now_iso()
    ei = data.get("rcsb_entry_info", {}) or {}
    ai = data.get("rcsb_accession_info", {}) or {}
    struct = data.get("struct", {}) or {}

    # Resolution: prefer the entry-level combined value; fall back to refinement d_res_high.
    resolution = None
    rc = ei.get("resolution_combined")
    if isinstance(rc, list) and rc:
        try:
            resolution = round(float(rc[0]), 2)
        except (TypeError, ValueError):
            resolution = None
    if resolution is None:
        refine = data.get("refine")
        if isinstance(refine, list) and refine:
            try:
                resolution = round(float(refine[0].get("ls_d_res_high")), 2)
            except (TypeError, ValueError, AttributeError):
                resolution = None

    # Experimental method: entry_info value is already tidy ("X-ray", "NMR", "EM");
    # fall back to the raw exptl method string if needed.
    method = ei.get("experimental_method")
    if not method:
        exptl = data.get("exptl")
        if isinstance(exptl, list) and exptl:
            method = exptl[0].get("method")

    rec = {
        "pdb_id": pdb_id.upper(),
        "available": True,
        "resolution_A": resolution,
        "experimental_method": method,
        "deposition_date": _clean_date(ai.get("deposit_date")),
        "release_date": _clean_date(ai.get("initial_release_date")),
        "title": struct.get("title"),
        "polymer_composition": ei.get("polymer_composition"),
        "polymer_entity_instance_count": ei.get("deposited_polymer_entity_instance_count"),
        "provenance": {
            "pdb_id": pdb_id.upper(),
            "source_url": source_url,
            "fetched_at": fetched_at,
            "api": API_LABEL,
        },
    }
    return rec


def build_unavailable_record(pdb_id, source_url, error, fetched_at=None):
    """Provenance-honest record for a failed fetch: no values, error recorded.

    This is what keeps the workflow from ever falling back to a typed default --
    the report reads these nulls and prints "unavailable".
    """
    fetched_at = fetched_at or _utc_now_iso()
    rec = {
        "pdb_id": pdb_id.upper(),
        "available": False,
        "provenance": {
            "pdb_id": pdb_id.upper(),
            "source_url": source_url,
            "fetched_at": fetched_at,
            "api": API_LABEL,
            "error": str(error),
        },
    }
    for f in FACT_FIELDS:
        rec[f] = None
    return rec


def fetch_structure_metadata(pdb_id, timeout=30):
    """Fetch and parse structure metadata for a PDB id from the RCSB Data REST API.

    Returns an `available: true` record on success. Raises on any network/HTTP/parse
    failure -- the CLI catches that and writes an `available: false` sidecar.
    """
    import urllib.request
    import urllib.error

    if not pdb_id or not str(pdb_id).strip():
        raise ValueError("empty PDB id")
    pid = str(pdb_id).strip()
    url = _entry_url(pid)
    fetched_at = _utc_now_iso()
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "biomni-binder-antibody-design"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    return parse_entry_json(pid, data, url, fetched_at=fetched_at)


def parse_args():
    p = argparse.ArgumentParser(
        description="Fetch PDB structure metadata from RCSB and write a provenance sidecar.")
    p.add_argument("--pdb", required=True, help="PDB id, e.g. 5O45.")
    p.add_argument("--out", required=True, help="Output structure_metadata.json path.")
    p.add_argument("--timeout", type=float, default=30, help="HTTP timeout (seconds).")
    return p.parse_args()


def main():
    args = parse_args()
    pid = args.pdb.strip()
    if not pid:
        eprint("[fetch_structure_metadata] ERROR: empty --pdb")
        sys.exit(2)

    url = _entry_url(pid)
    exit_code = 0
    try:
        rec = fetch_structure_metadata(pid, timeout=args.timeout)
        res = rec.get("resolution_A")
        res_str = f"{res:.2f} A" if isinstance(res, (int, float)) else "unavailable"
        print(f"[fetch_structure_metadata] {rec['pdb_id']}: {rec.get('experimental_method') or 'method unavailable'}, "
              f"resolution {res_str}, deposited {rec.get('deposition_date') or 'unavailable'}")
    except Exception as e:  # noqa: BLE001 -- any failure becomes an honest "unavailable" record
        eprint(f"[fetch_structure_metadata] WARNING: fetch failed for {pid}: {e!r}")
        eprint("[fetch_structure_metadata] writing an 'available: false' sidecar "
               "(the report will say 'unavailable'; it never falls back to a typed default).")
        rec = build_unavailable_record(pid, url, e)
        exit_code = 3

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(rec, fh, indent=1)
    print(f"[fetch_structure_metadata] wrote {args.out}")
    print(json.dumps(rec, indent=1))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

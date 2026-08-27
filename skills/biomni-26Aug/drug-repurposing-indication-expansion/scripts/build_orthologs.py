"""Build a mouse->human ortholog map from the MGI homology table.

Strategy (per plan): download the MGI table at runtime; fall back to the bundled
copy in assets/ if the download fails. Deterministic and offline-safe.

MGI table URL (verified HTTP 200):
    https://www.informatics.jax.org/downloads/reports/HOM_MouseHumanSequence.rpt

Public API:
    load_ortholog_map(workdir) -> dict {UPPER_mouse_symbol: set(UPPER_human_symbols)}
"""
import os
import sys
import pandas as pd

MGI_URL = "https://www.informatics.jax.org/downloads/reports/HOM_MouseHumanSequence.rpt"
BUNDLED = os.path.join(os.path.dirname(__file__), "..", "assets", "HOM_MouseHumanSequence.rpt")


def _ensure_table(workdir):
    """Return a path to the MGI .rpt, downloading it if needed, else the bundled copy."""
    os.makedirs(workdir, exist_ok=True)
    dest = os.path.join(workdir, "HOM_MouseHumanSequence.rpt")
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        return dest
    # Try runtime download
    try:
        import urllib.request
        urllib.request.urlretrieve(MGI_URL, dest)
        if os.path.getsize(dest) > 1_000_000:
            print(f"[orthologs] downloaded MGI table -> {dest} ({os.path.getsize(dest)} bytes)")
            return dest
    except Exception as e:  # noqa: BLE001
        print(f"[orthologs] download failed ({e}); using bundled fallback", file=sys.stderr)
    # Fallback to bundled asset
    bundled = os.path.abspath(BUNDLED)
    if os.path.exists(bundled) and os.path.getsize(bundled) > 1_000_000:
        print(f"[orthologs] using bundled MGI table -> {bundled}")
        return bundled
    raise FileNotFoundError(
        "Could not obtain the MGI ortholog table by download or bundled fallback. "
        f"Tried URL {MGI_URL} and asset {bundled}.")


def load_ortholog_map(workdir="/workspace/dri_run/data"):
    """Build {UPPER mouse symbol -> set(UPPER human symbols)} grouped by DB Class Key."""
    path = _ensure_table(workdir)
    df = pd.read_csv(path, sep="\t", dtype=str)
    # Columns of interest: 'DB Class Key', 'Common Organism Name', 'Symbol'
    org_col = "Common Organism Name"
    sym_col = "Symbol"
    key_col = "DB Class Key"
    for c in (org_col, sym_col, key_col):
        if c not in df.columns:
            raise ValueError(f"MGI table missing expected column '{c}'. Columns: {list(df.columns)}")
    df["is_human"] = df[org_col].str.contains("human", case=False, na=False)
    df["is_mouse"] = df[org_col].str.contains("mouse", case=False, na=False)
    m2h = {}
    for key, grp in df.groupby(key_col):
        humans = {s.upper() for s in grp.loc[grp["is_human"], sym_col].dropna()}
        mice = {s.upper() for s in grp.loc[grp["is_mouse"], sym_col].dropna()}
        if not humans:
            continue
        for m in mice:
            m2h.setdefault(m, set()).update(humans)
    print(f"[orthologs] built map: {len(m2h)} mouse symbols -> human orthologs")
    return m2h


if __name__ == "__main__":
    wd = sys.argv[1] if len(sys.argv) > 1 else "/workspace/dri_run/data"
    m = load_ortholog_map(wd)
    # tiny sanity print
    for probe in ("SPP1", "TP53", "GAPDH"):
        print(probe, "->", sorted(m.get(probe, []))[:5])

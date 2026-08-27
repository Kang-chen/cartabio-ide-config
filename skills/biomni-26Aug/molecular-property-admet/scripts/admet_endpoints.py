"""
Canonical ADMET endpoint registry.

ADMET-AI column names vary across versions and are matched by ad-hoc keyword
searches scattered across the plotting/summary code. That is fragile: a renamed
column silently disappears from plots, and loose substrings collide with unrelated
columns (e.g. "ames" matching "names_aggregated").

This module centralizes the mapping: a canonical endpoint key -> ordered list of
known column aliases, plus one resolver that all consumers share. Matching is
exact-or-prefix (not arbitrary substring), so it won't collide with non-endpoint
columns, and `*_percentile` companion columns are excluded once, here.
"""

# Canonical key -> aliases, most specific first. Aliases match a column when the
# column name equals the alias or starts with it (case-insensitive).
ENDPOINT_ALIASES = {
    # Toxicity (binary; higher = worse)
    "hERG": ["hERG"],
    "AMES": ["AMES"],
    "DILI": ["DILI"],
    "Carcinogenicity": ["Carcinogens_Lagunin", "Carcinogen"],
    "ClinTox": ["ClinTox"],
    # Distribution / absorption
    "BBB": ["BBB_Martins", "BBB"],
    "Caco-2": ["Caco2_Wang", "Caco2"],
    "HIA": ["HIA_Hou", "HIA"],
    "Pgp": ["Pgp_Broccatelli", "Pgp"],
    "Bioavailability": ["Bioavailability_Ma", "Bioavailability"],
    "Solubility": ["Solubility_AqSolDB", "Solubility"],
    # Metabolism — CYP inhibition (Veith). Specific alias first so we don't grab
    # the *_Substrate_* columns by mistake.
    "CYP1A2": ["CYP1A2_Veith", "CYP1A2"],
    "CYP2C19": ["CYP2C19_Veith", "CYP2C19"],
    "CYP2C9": ["CYP2C9_Veith", "CYP2C9"],
    "CYP2D6": ["CYP2D6_Veith", "CYP2D6"],
    "CYP3A4": ["CYP3A4_Veith", "CYP3A4"],
}

# Subset surfaced as hard safety flags in the summary / report.
SAFETY_KEYS = ["hERG", "AMES", "DILI"]

# CYP inhibition keys (for any CYP-focused view).
CYP_KEYS = ["CYP1A2", "CYP2C19", "CYP2C9", "CYP2D6", "CYP3A4"]


def resolve_endpoints(columns, verbose=True):
    """
    Map canonical endpoint keys to the actual columns present.

    Parameters
    ----------
    columns : iterable of str
        The DataFrame columns to search (typically ``df.columns``).
    verbose : bool
        Print a one-line found/missing summary.

    Returns
    -------
    dict
        ``{canonical_key: actual_column_name_or_None}`` for every key in
        ENDPOINT_ALIASES.
    """
    # Exclude the DrugBank percentile companion columns once, here.
    cols = [c for c in columns if "percentile" not in str(c).lower()]

    resolved = {}
    for key, aliases in ENDPOINT_ALIASES.items():
        match = None
        for alias in aliases:
            al = alias.lower()
            for c in cols:
                cl = str(c).lower()
                if cl == al or cl.startswith(al):
                    match = c
                    break
            if match:
                break
        resolved[key] = match

    if verbose:
        found = [k for k, v in resolved.items() if v]
        missing = [k for k, v in resolved.items() if not v]
        msg = f"   Resolved {len(found)}/{len(resolved)} ADMET endpoints"
        if missing:
            msg += f"; missing: {', '.join(missing)}"
        print(msg)

    return resolved


def resolved_columns(columns, keys=None, verbose=False):
    """Return the list of actual columns for the requested canonical keys (in key order)."""
    resolved = resolve_endpoints(columns, verbose=verbose)
    keys = keys if keys is not None else list(ENDPOINT_ALIASES)
    return [resolved[k] for k in keys if resolved.get(k)]

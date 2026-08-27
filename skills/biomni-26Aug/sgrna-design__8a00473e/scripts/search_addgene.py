"""
Tier 1 / Method 1: Search the bundled Addgene validated-sgRNA database.

The bundled CSV (`references/resource/addgene_grna_sequences.csv`, 321 rows / 197 genes)
comes straight from Addgene's gRNA reference export. Two quirks in the raw file that the
original guide's example code did NOT handle and that silently break naive parsing:

  1. Real column names use SPACES, not underscores:
       'Application', 'Cas9 Species', 'Depositor', 'Plasmid ID', 'PubMed ID',
       'Target Gene', 'Target Sequence', 'Target Species'
     (the guide's snippets reference 'Target_Gene', 'Target_Species', etc.)

  2. 'Plasmid ID' and 'PubMed ID' cells are wrapped in HTML <a> tags, e.g.
       '<a href="/58252/">58252</a>'
       '<a href="https://www.ncbi.nlm.nih.gov/pubmed/24870050/">24870050</a>'
     so the visible ID and its URL must be extracted from the markup.

  3. Species/application values are messy: 'H. sapiens' vs 'C.elegans' vs 'C. elegans',
     'Synthetic' vs 'synthetic', trailing '&nbsp;'. Matching normalizes whitespace + case.

This module returns clean, de-HTML'd records suitable for citation.
"""

from __future__ import annotations

import html
import os
import re
import pandas as pd

# Resolve the bundled CSV relative to this script so the skill is standalone.
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.normpath(
    os.path.join(_HERE, "..", "references", "resource", "addgene_grna_sequences.csv")
)

# Map user-facing intent -> Addgene 'Application' vocabulary actually present in the file.
# (Observed values: cut, activate, interfere, tag, nick, scaffold, visualize,
#  'RNA targeting', purify, methylation, 'activate/interfere', 'cut/nick', etc.)
APPLICATION_SYNONYMS = {
    "knockout": ["cut", "cut/nick"],
    "ko": ["cut", "cut/nick"],
    "cut": ["cut", "cut/nick"],
    "activation": ["activate", "activate/interfere"],
    "crispra": ["activate", "activate/interfere"],
    "activate": ["activate", "activate/interfere"],
    "inhibition": ["interfere", "rna targeting", "activate/interfere"],
    "interference": ["interfere", "rna targeting", "activate/interfere"],
    "crispri": ["interfere", "rna targeting", "activate/interfere"],
    "knockdown": ["interfere", "rna targeting"],
}

_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"')


def _strip_html_text(cell: str) -> str:
    """Return the visible text of an HTML cell (e.g. '<a ...>58252</a>' -> '58252')."""
    if not isinstance(cell, str):
        return "" if pd.isna(cell) else str(cell)
    text = _TAG_RE.sub("", cell)
    return html.unescape(text).strip()


def _extract_href(cell: str, base: str = "") -> str:
    """Return the href URL inside an HTML cell, optionally prefixing a base for relative paths."""
    if not isinstance(cell, str):
        return ""
    m = _HREF_RE.search(cell)
    if not m:
        return ""
    url = html.unescape(m.group(1)).strip()
    # Placeholder hrefs with no real ID (e.g. "", "/", "//") -> return blank.
    if url.strip("/") == "":
        return ""
    if url.startswith("/") and base:
        url = base.rstrip("/") + url
    return url


def _norm(s: str) -> str:
    """Normalize a string for matching: unescape entities, collapse whitespace, lowercase."""
    if not isinstance(s, str):
        return ""
    s = html.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def load_addgene(csv_path: str = DEFAULT_CSV) -> pd.DataFrame:
    """Load the bundled CSV and add cleaned helper columns (does not drop originals)."""
    df = pd.read_csv(csv_path)
    df = df.copy()
    df["plasmid_id"] = df["Plasmid ID"].apply(_strip_html_text)
    df["plasmid_url"] = df["Plasmid ID"].apply(
        lambda c: _extract_href(c, base="https://www.addgene.org")
    )
    df["pubmed_id"] = df["PubMed ID"].apply(_strip_html_text)
    df["pubmed_url"] = df["PubMed ID"].apply(_extract_href)
    df["_gene_norm"] = df["Target Gene"].apply(_norm)
    df["_species_norm"] = df["Target Species"].apply(_norm)
    df["_application_norm"] = df["Application"].apply(_norm)
    return df


def search_addgene(
    gene: str,
    species: str | None = None,
    application: str | None = None,
    csv_path: str = DEFAULT_CSV,
) -> pd.DataFrame:
    """
    Search the bundled Addgene validated-sgRNA database.

    Parameters
    ----------
    gene : str
        Gene symbol (case-insensitive), e.g. "TP53", "AAVS1".
    species : str, optional
        Organism filter, matched loosely (e.g. "H. sapiens", "human", "mouse").
        Common aliases handled: human->h. sapiens, mouse->m. musculus, rat->r. norvegicus.
    application : str, optional
        Intent: "knockout"/"cut", "activation"/"CRISPRa", "inhibition"/"CRISPRi", etc.
        Mapped to the file's vocabulary via APPLICATION_SYNONYMS.
    csv_path : str
        Path to the bundled CSV (defaults to the file inside this skill).

    Returns
    -------
    pandas.DataFrame
        Matching rows with clean columns:
        ['Target Gene', 'Target Sequence', 'Target Species', 'Application',
         'Cas9 Species', 'plasmid_id', 'plasmid_url', 'pubmed_id', 'pubmed_url', 'Depositor'].
        Empty DataFrame (with those columns) if no match — caller MUST then run Method 2.
    """
    df = load_addgene(csv_path)

    out_cols = [
        "Target Gene", "Target Sequence", "Target Species", "Application",
        "Cas9 Species", "plasmid_id", "plasmid_url", "pubmed_id", "pubmed_url", "Depositor",
    ]

    mask = df["_gene_norm"] == _norm(gene)

    if species:
        sp = _norm(species)
        species_aliases = {
            "human": "h. sapiens", "mouse": "m. musculus", "rat": "r. norvegicus",
            "zebrafish": "d. rerio", "fly": "d. melanogaster", "yeast": "s. cerevisiae",
            "worm": "c. elegans",
        }
        sp = species_aliases.get(sp, sp)
        # Loose contains match to absorb messy variants ('C.elegans', trailing spaces).
        sp_compact = sp.replace(" ", "")
        mask &= df["_species_norm"].str.replace(" ", "", regex=False).str.contains(
            re.escape(sp_compact), na=False
        )

    if application:
        wanted = APPLICATION_SYNONYMS.get(_norm(application), [_norm(application)])
        mask &= df["_application_norm"].isin(wanted)

    res = df.loc[mask, out_cols].reset_index(drop=True)
    return res


if __name__ == "__main__":
    import sys

    g = sys.argv[1] if len(sys.argv) > 1 else "TP53"
    sp = sys.argv[2] if len(sys.argv) > 2 else None
    app = sys.argv[3] if len(sys.argv) > 3 else None
    r = search_addgene(g, sp, app)
    print(f"Found {len(r)} validated sgRNA(s) for {g}"
          + (f" / {sp}" if sp else "") + (f" / {app}" if app else ""))
    if len(r):
        with pd.option_context("display.max_columns", None, "display.width", 200):
            print(r.to_string(index=False))
    else:
        print("No Addgene match -> you MUST still run Method 2 (literature search) "
              "before proceeding to Option 2 (CRISPick).")

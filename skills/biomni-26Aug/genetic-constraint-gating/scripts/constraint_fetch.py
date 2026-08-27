"""
constraint_fetch.py — resolve genes and fetch gnomAD LoF constraint + grounded disease notes.

Data sources (both verified live):
  - gnomAD GraphQL API: gene-level LoF constraint (LOEUF, pLI, o/e, LoF Z) for v2.1.1 and v4.1.
  - MyGene.info: symbol/alias/Ensembl resolution + ClinGen disease/inheritance grounding.

Design rules:
  - LOEUF is gnomAD field `oe_lof_upper` (upper bound of the o/e 90% CI); LOWER = more constrained.
  - Standard intolerance flag (computed on v2.1.1): LOEUF < 0.35 OR pLI >= 0.90.
  - Never fabricate: a gene with no gnomAD record is reported as not-available with a reason;
    a gene with no ClinGen record gets disease_source='none' and an explicit not-retrieved label.
"""

import random
import time
import requests

GNOMAD_API = "https://gnomad.broadinstitute.org/api"
MYGENE_QUERY = "https://mygene.info/v3/query"
MYGENE_GENE = "https://mygene.info/v3/gene/{}"

LOEUF_CUT = 0.35
PLI_CUT = 0.90

# gnomAD dataset -> reference genome the GraphQL schema expects
VERSION_GENOME = {"v2.1.1": "GRCh37", "v4.1": "GRCh38"}


# --------------------------------------------------------------------------- #
# Low-level HTTP with retry/backoff (gnomAD GraphQL throws transient errors)
# --------------------------------------------------------------------------- #
def _post_graphql(query, retries=5, timeout=90):
    last = None
    for i in range(retries):
        try:
            r = requests.post(GNOMAD_API, json={"query": query},
                              headers={"Content-Type": "application/json"}, timeout=timeout)
            if r.status_code == 200:
                js = r.json()
                errs = js.get("errors")
                if errs:
                    # transient "please try again" style errors -> back off and retry
                    if any("try again" in (e.get("message", "").lower()) for e in errs):
                        last = errs
                        time.sleep(2 ** i)
                        continue
                return js
            last = f"HTTP {r.status_code}"
            time.sleep(2 ** i)
        except Exception as e:  # network hiccup
            last = str(e)
            time.sleep(2 ** i)
    return {"errors": [{"message": f"gnomAD request failed after {retries} retries: {last}"}]}


def _get_json(url, params, retries=4, timeout=45):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            time.sleep(1.5 * (i + 1))
        except Exception:
            time.sleep(1.5 * (i + 1))
    return {}


# --------------------------------------------------------------------------- #
# 1. Gene resolution (symbol / alias / Ensembl ID -> current symbol + ENSG)
# --------------------------------------------------------------------------- #
def resolve_gene(user_input):
    """
    Resolve a user token (current symbol, deprecated symbol/alias, or ENSG id)
    to a dict: {input_as, symbol, ensembl, entrez, alias_note}.
    Returns symbol=None if it cannot be resolved.
    """
    token = str(user_input).strip()
    out = {"input_as": token, "symbol": None, "ensembl": None,
           "entrez": None, "alias_note": ""}
    if not token:
        return out

    is_ensg = token.upper().startswith("ENSG")
    if is_ensg:
        q = f'ensembl.gene:{token.split(".")[0]}'
    else:
        # match the token as an exact symbol OR as an alias so deprecated names resolve
        q = f'symbol:{token} OR alias:{token}'

    js = _get_json(MYGENE_QUERY, {"q": q, "species": "human",
                                  "fields": "symbol,ensembl.gene,entrezgene,alias"})
    hits = js.get("hits", []) if isinstance(js, dict) else []
    # Prefer an exact symbol match; else the top-scoring protein-coding-looking hit
    chosen = None
    for h in hits:
        if not is_ensg and h.get("symbol", "").upper() == token.upper():
            chosen = h
            break
    if chosen is None and hits:
        # avoid antisense/-AS1 style partial matches when a cleaner hit exists
        non_as = [h for h in hits if not h.get("symbol", "").upper().endswith(("-AS1", "-AS2"))]
        chosen = (non_as or hits)[0]

    if chosen:
        out["symbol"] = chosen.get("symbol")
        ens = chosen.get("ensembl")
        if isinstance(ens, list):
            ens = ens[0] if ens else {}
        out["ensembl"] = (ens or {}).get("gene")
        out["entrez"] = chosen.get("entrezgene") or chosen.get("_id")
        if out["symbol"] and out["symbol"].upper() != token.upper() and not is_ensg:
            out["alias_note"] = f"input '{token}' resolved to '{out['symbol']}'"
    return out


# --------------------------------------------------------------------------- #
# 2. gnomAD constraint fetch (per version)
# --------------------------------------------------------------------------- #
def fetch_constraint(symbol, version, null_retries=8):
    """Return the gnomad_constraint dict for a symbol in a given gnomAD version, or None.

    gnomAD intermittently returns a 200 response with ``data.gene == null`` for a
    gene that does have a constraint record (observed across runs for TP53, SCN1A,
    PTEN, PCSK9, MECP2). Unlike the explicit "try again" GraphQL errors handled in
    ``_post_graphql()``, these silent nulls are not retried there. We re-query here
    with bounded backoff before giving up and reporting the gene as not-available.

    The default of 8 attempts reflects empirical observation that the documented
    flaky genes (TP53, SCN1A, PTEN, PCSK9, MECP2) can require several re-queries
    before the real record is returned; 3 was insufficient in practice.
    """
    genome = VERSION_GENOME[version]
    q = ('{gene(gene_symbol:"%s",reference_genome:%s){symbol gene_id '
         'gnomad_constraint{pLI oe_lof oe_lof_lower oe_lof_upper oe_lof_percentile '
         'lof_z obs_lof exp_lof oe_mis mis_z}}}'
         % (symbol, genome))
    gene = None
    for attempt in range(null_retries):
        js = _post_graphql(q)
        # surface hard GraphQL/network errors immediately rather than masking them
        if js.get("errors") and not js.get("data"):
            return None
        gene = (js.get("data") or {}).get("gene") if js.get("data") else None
        if gene:
            break
        if attempt < null_retries - 1:
            time.sleep(1.5 * (attempt + 1) + random.uniform(0, 0.5))  # 1.5s+jitter, 3.0s+jitter, ...
    if not gene:
        return None
    c = gene.get("gnomad_constraint")
    if c:
        c = dict(c)
        c["_gene_id"] = gene.get("gene_id")
    return c


# --------------------------------------------------------------------------- #
# 3. Grounded disease / inheritance note (MyGene.info + ClinGen)
# --------------------------------------------------------------------------- #
_MOI = {"AD": "autosomal dominant", "AR": "autosomal recessive",
        "XL": "X-linked", "XLD": "X-linked dominant", "XLR": "X-linked recessive",
        "MT": "mitochondrial", "SD": "semidominant"}


def fetch_disease(entrez):
    """
    Return a grounded disease/inheritance note from ClinGen curation via MyGene.info.
    Never fabricates: if no curated association exists, disease_source='none'.
    """
    out = {"gene_name": None, "gene_mim": None, "disease_label": None,
           "inheritance": None, "mondo_id": None, "disease_source": "none"}
    if not entrez:
        return out
    js = _get_json(MYGENE_GENE.format(entrez),
                   {"fields": "symbol,name,summary,MIM,clingen"})
    if not isinstance(js, dict) or not js:
        return out
    out["gene_name"] = js.get("name")
    mim = js.get("MIM")
    out["gene_mim"] = mim[0] if isinstance(mim, list) else mim

    clingen = js.get("clingen")
    cv = (clingen or {}).get("clinical_validity") if isinstance(clingen, dict) else None
    if cv:
        if isinstance(cv, dict):
            cv = [cv]
        # prefer the strongest classification (definitive > strong > moderate > ...)
        rank = {"definitive": 0, "strong": 1, "moderate": 2, "limited": 3,
                "disputed": 4, "refuted": 5, "no known disease relationship": 6}
        cv_sorted = sorted(cv, key=lambda x: rank.get(str(x.get("classification", "")).lower(), 9))
        labels = []
        seen = set()
        for rec in cv_sorted[:3]:
            lab = rec.get("disease_label")
            if lab and lab not in seen:
                seen.add(lab)
                moi = rec.get("moi")
                cls = rec.get("classification")
                tag = lab
                extra = []
                if cls:
                    extra.append(cls)
                if moi:
                    extra.append(_MOI.get(moi, moi))
                if extra:
                    tag += f" ({'; '.join(extra)})"
                labels.append(tag)
        if labels:
            top = cv_sorted[0]
            out["disease_label"] = "; ".join(labels)
            out["inheritance"] = _MOI.get(top.get("moi"), top.get("moi"))
            out["mondo_id"] = top.get("mondo")
            out["disease_source"] = "ClinGen (via MyGene.info)"
    return out

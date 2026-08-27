#!/usr/bin/env python3
"""
screen_and_extract.py -- PRISMA-style screening scaffold + extraction templates.

IMPORTANT DIVISION OF LABOR
  Screening (benchmark vs method vs review vs off-topic) and extracting nuanced
  performance claims are JUDGMENT tasks. A keyword script cannot do them reliably from
  free-text abstracts. So this script:
    * produces a DRAFT screening_log.csv using transparent keyword heuristics that the
      agent should review and correct while reading abstracts, and
    * writes EMPTY, well-formed template files (comparison_matrix / benchmark_catalog /
      performance_claims for comparison mode; evidence_table for topic mode) for the agent
      to populate with source-bound values (ideally from targeted full-text reads).
  Curated, source-bound values beat raw abstract mining (a lesson from the reference run).

Usage:
  python screen_and_extract.py --corpus corpus.csv --task "..." \
      --methods "DESeq2,edgeR,limma-voom" --mode comparison --out RUN_DIR
"""
import argparse, csv, json, os, re, sys

BENCH_KW = ["benchmark", "comparison", "evaluat", "accuracy", "false discovery",
            "sensitivity", "specificity", "assessment", "concordance", "roc", "auc",
            "ground truth", "spike-in", "simulat"]
METHOD_KW = ["we present", "we introduce", "we propose", "novel method", "new method",
             "algorithm", "we develop", "toolkit", "package", "framework"]
REVIEW_KW = ["review", "survey", "overview", "perspective", "state of the art"]
EXCL_CLINICAL = ["clinical trial", "randomized", "patients were treated", "phase iii",
                 "phase ii trial"]
EXCL_PRECLIN = ["mouse model", "in vivo", "xenograft", "knockout mice", "rat model"]


def _read_corpus(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def _txt(r):
    return f"{r.get('title','')} {r.get('abstract','')} {r.get('keywords','')}".lower()


def draft_screen(rows, task, methods, mode):
    """Transparent keyword screening -> draft decisions the agent must review."""
    methods_l = [m.lower() for m in methods]
    task_tokens = [t for t in re.split(r"[^a-z0-9]+", task.lower()) if len(t) > 3]
    log = []
    for r in rows:
        t = _txt(r)
        mentions_method = any(m in t for m in methods_l)
        on_topic = mentions_method or any(tok in t for tok in task_tokens)
        decision, label, reason = "include", "other", ""

        if not on_topic:
            decision, label, reason = "exclude", "", "off_topic"
        elif any(k in t for k in EXCL_CLINICAL) and not any(k in t for k in BENCH_KW):
            decision, label, reason = "exclude", "", "clinical"
        elif any(k in t for k in EXCL_PRECLIN) and not any(k in t for k in BENCH_KW):
            decision, label, reason = "exclude", "", "preclinical"
        else:
            if any(k in t for k in BENCH_KW):
                label = "benchmark" if ("benchmark" in t or "simulat" in t
                                        or "ground truth" in t) else "comparison"
            elif any(k in t for k in REVIEW_KW):
                label = "review"
            elif any(k in t for k in METHOD_KW) and mentions_method:
                label = "method"
            else:
                label = "other"
        log.append({
            "doi": r.get("doi", ""), "title": r.get("title", ""),
            "decision": decision, "label": label, "exclude_reason": reason,
        })
    return log


def write_csv(rows, cols, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def write_templates(out_dir, mode):
    """Empty, schema-correct templates for the agent to populate (source-bound)."""
    made = []
    if mode == "comparison":
        # comparison_matrix.csv (header-only; agent fills dimensions x methods)
        p = os.path.join(out_dir, "comparison_matrix.csv")
        with open(p, "w", newline="") as f:
            csv.writer(f).writerow(["Dimension"])  # methods added by agent as columns
        made.append(p)

        bench_cols = ["benchmark_name", "benchmark_type", "organism", "truth_basis",
                      "key_metric", "defining_paper", "doi"]
        claim_cols = ["method", "dimension", "finding", "benchmark", "source", "doi",
                      "evidence_thickness"]
        for name, cols in [("benchmark_catalog", bench_cols),
                           ("performance_claims", claim_cols)]:
            write_csv([], cols, os.path.join(out_dir, f"{name}.csv"))
            with open(os.path.join(out_dir, f"{name}.json"), "w") as f:
                json.dump([], f, indent=2)
            made += [os.path.join(out_dir, f"{name}.csv"),
                     os.path.join(out_dir, f"{name}.json")]
    else:
        ev_cols = ["theme", "finding", "study_type", "effect_or_metric", "source", "doi"]
        write_csv([], ev_cols, os.path.join(out_dir, "evidence_table.csv"))
        made.append(os.path.join(out_dir, "evidence_table.csv"))
    return made


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--methods", default="")
    ap.add_argument("--mode", default="comparison", choices=["comparison", "topic"])
    ap.add_argument("--out", required=True, help="run directory")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    methods = [m.strip() for m in args.methods.replace(";", ",").split(",") if m.strip()]
    rows = _read_corpus(args.corpus)
    log = draft_screen(rows, args.task, methods, args.mode)
    write_csv(log, ["doi", "title", "decision", "label", "exclude_reason"],
              os.path.join(args.out, "screening_log.csv"))

    inc = [x for x in log if x["decision"] == "include"]
    from collections import Counter
    labels = Counter(x["label"] for x in inc)
    reasons = Counter(x["exclude_reason"] for x in log if x["decision"] == "exclude")
    templates = write_templates(args.out, args.mode)

    print(f"Screened {len(log)} records: {len(inc)} include / {len(log)-len(inc)} exclude")
    print(f"  include labels: {dict(labels)}")
    print(f"  exclude reasons: {dict(reasons)}")
    print(f"  wrote DRAFT screening_log.csv + {len(templates)} empty templates")
    print("  NOTE: review draft decisions and populate templates with source-bound values.")
    return {"screening_log": log, "templates": templates}


if __name__ == "__main__":
    main()

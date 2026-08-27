#!/usr/bin/env python3
"""
fold_orchestrate.py — robust, unattended protein-folding driver for the
protein-structure-prediction skill.

Design goals (why this file exists):
  * NEVER block on an HPC completion callback. Submit a job, then POLL
    `hpc_get_job_results()` with a BOUNDED timeout.
  * Pick the default predictor by SEQUENCE SIZE: ESMCFold2 (fast, MSA-free) for
    sequences within its supported range, AlphaFold only when it is the right
    tool (long sequence or explicit request).
  * On stall / timeout / empty output, FALL BACK to a faster predictor (or
    proceed with whatever finished), so a run always yields a deliverable.
  * Record the chosen predictor and the full fallback trail to a JSON manifest.

One public entrypoint: `orchestrate_fold(...)`. Also runnable as a CLI.

The per-method HPC commands/flags below are copied from the skill's verified
`references/methods_reference.md` (confirmed on real Biomni HPC jobs). Do not
"simplify" the mandatory flags — several avoid segfaults/crashes on this
environment's GPUs.

Per-residue pLDDT extraction is delegated to the sibling `extract_plddt.py`
(unchanged, already verified).
"""
import os
import sys
import json
import time
import argparse

# extract_plddt lives next to this file
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_plddt as _xp  # noqa: E402


# ----------------------------------------------------------------------------
# HPC helpers (imported lazily so the selection logic can be unit-tested
# without a live HPC session / biomni install).
# ----------------------------------------------------------------------------
def _hpc():
    from biomni.tool import (hpc_run_tool, hpc_get_job_results,
                             hpc_get_logs, hpc_cancel_job)
    return hpc_run_tool, hpc_get_job_results, hpc_get_logs, hpc_cancel_job


# ----------------------------------------------------------------------------
# Defaults / policy
# ----------------------------------------------------------------------------
DEFAULT_ESMFOLD_MAX_LEN = 400      # ESMCFold2-default boundary (A1)
DEFAULT_POLL_TIMEOUT_S = 900       # ~15 min per job (A4)
DEFAULT_POLL_INTERVAL_S = 30
_TERMINAL_OK = {"completed", "succeeded", "success", "done", "finished"}
_TERMINAL_BAD = {"failed", "cancelled", "canceled", "error", "stopped", "timeout"}


# ----------------------------------------------------------------------------
# Predictor selection by size (A2). Pure function — no HPC needed.
# ----------------------------------------------------------------------------
def choose_predictor(seq_len, methods=None,
                     esmfold_max_len=DEFAULT_ESMFOLD_MAX_LEN):
    """Return (primary, fallback_chain) method-key lists for a MONOMER.

    This orchestrator's automated path is single-chain only (see module and
    SKILL.md scope). Multi-chain complexes are handled manually per
    references/methods_reference.md (AlphaFold-multimer / Boltz / Chai) and are
    rejected up front by orchestrate_fold(), so there is no complex branch here.

    methods: explicit user pick(s) (list of keys) overrides the size rule, but
             the size-based fallback chain is still appended for robustness.
    """
    valid = {"esmfold", "alphafold", "boltz", "chai"}
    if methods:
        methods = [m.lower() for m in methods]
        for m in methods:
            if m not in valid:
                raise ValueError(f"unknown method {m!r}; valid: {sorted(valid)}")
        primary = methods[0]
        explicit_rest = methods[1:]
    else:
        primary = None
        explicit_rest = []

    # size-based default primary (monomer)
    if primary is None:
        if seq_len <= esmfold_max_len:
            primary = "esmfold"            # fast, MSA-free (A2)
        else:
            primary = "alphafold"          # beyond ESMCFold2 default range

    # build fallback chain (A3), never including primary, de-duplicated
    chain = list(explicit_rest)
    if seq_len <= esmfold_max_len:
        size_chain = ["esmfold", "boltz"]  # guaranteed-finish first
    else:
        size_chain = ["boltz", "esmfold"]  # Boltz handles long seqs; ESMCFold2 last resort
    for m in size_chain:
        if m not in chain:
            chain.append(m)
    chain = [m for m in chain if m != primary]
    return primary, chain


# ----------------------------------------------------------------------------
# Per-method HPC command + input file construction (verified flags).
# ----------------------------------------------------------------------------
def _build_job(method, name, seq, workdir, esmfold_max_len):
    """Return (tool_id, command, input_files dict, dest_filename)."""
    os.makedirs(workdir, exist_ok=True)
    if method == "alphafold":
        fasta = os.path.join(workdir, f"{name}.fasta")
        with open(fasta, "w") as fh:
            fh.write(f">{name}\n{seq}\n")
        cmd = (
            "python3 /app/alphafold/run_alphafold.py "
            f"--fasta_paths=/input/{name}.fasta "
            "--output_dir=/output "
            "--data_dir=/mnt/fsx/dbs/alphafold2 "
            "--uniref90_database_path=/mnt/fsx/dbs/alphafold2/uniref90/uniref90.fasta "
            "--mgnify_database_path=/mnt/fsx/dbs/alphafold2/mgnify/mgy_clusters_2022_05.fa "
            "--small_bfd_database_path=/mnt/fsx/dbs/alphafold2/small_bfd/bfd-first_non_consensus_sequences.fasta "
            "--pdb70_database_path=/mnt/fsx/dbs/alphafold2/pdb70/pdb70 "
            "--template_mmcif_dir=/mnt/fsx/dbs/alphafold2/pdb_mmcif/mmcif_files "
            "--obsolete_pdbs_path=/mnt/fsx/dbs/alphafold2/pdb_mmcif/obsolete.dat "
            "--max_template_date=2024-01-01 "
            "--model_preset=monomer_ptm "
            "--db_preset=reduced_dbs "
            "--use_gpu_relax=false"
        )
        return "alphafold-v2", cmd, {f"{name}.fasta": fasta}

    if method == "esmfold":
        fasta = os.path.join(workdir, f"{name}.fasta")
        with open(fasta, "w") as fh:
            fh.write(f">{name}\n{seq}\n")
        # pass --max-length = max(len, 200 floor is implicit) so >200 aa is accepted
        max_len = max(int(esmfold_max_len), len(seq))
        cmd = (f"esmc fold --model full --input /input/{name}.fasta "
               f"--output /output --max-length {max_len}")
        return "esmcfold2", cmd, {f"{name}.fasta": fasta}

    if method == "boltz":
        yaml = os.path.join(workdir, f"{name}.yaml")
        with open(yaml, "w") as fh:
            fh.write("version: 1\nsequences:\n  - protein:\n"
                     "      id: A\n"
                     f"      sequence: {seq}\n")
        cmd = (f"HF_HUB_OFFLINE=1 boltz predict /input/{name}.yaml "
               "--out_dir /output --cache /mnt/fsx/dbs/boltz/cache "
               "--num_workers 0 --use_msa_server --no_kernels")
        return "boltz-2", cmd, {f"{name}.yaml": yaml}

    if method == "chai":
        fasta = os.path.join(workdir, f"{name}.fasta")
        with open(fasta, "w") as fh:
            fh.write(f">protein|name={name}\n{seq}\n")
        cmd = f"chai-lab fold --use-msa-server /input/{name}.fasta /output"
        return "chai-1", cmd, {f"{name}.fasta": fasta}

    raise ValueError(f"no job builder for method {method!r}")


# ----------------------------------------------------------------------------
# Bounded poll loop — the heart of the "never wait for a callback" contract.
# ----------------------------------------------------------------------------
def _poll_until_done(get_results, job_id, timeout_s, interval_s, log=print):
    """Poll a job with a bounded wall-clock timeout.

    Returns (outcome, results_dict) where outcome is one of:
      'completed'      -> terminal-good status with >=1 output file
      'failed'         -> terminal-bad status
      'timeout'        -> exceeded timeout (treated as stalled)
      'empty'          -> terminal-good but 0 output files
    """
    start = time.time()
    last = None
    while True:
        try:
            r = get_results(job_id)
        except Exception as e:
            log(f"    poll exception (will retry): {e!r}")
            r = last or {"status": "unknown", "files": []}
        last = r
        status = str(r.get("status", "")).lower()
        nfiles = len(r.get("files") or [])
        elapsed = time.time() - start
        log(f"    [{elapsed:6.0f}s] status={status or '?'} files={nfiles}")

        if status in _TERMINAL_OK:
            return ("completed" if nfiles > 0 else "empty", r)
        if status in _TERMINAL_BAD:
            return ("failed", r)
        if elapsed >= timeout_s:
            # bounded timeout: declare stalled (esp. AlphaFold MSA search)
            return ("timeout", r)
        time.sleep(interval_s)


# ----------------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------------
def orchestrate_fold(sequence, name, out_dir, methods=None, is_complex=False,
                     esmfold_max_len=DEFAULT_ESMFOLD_MAX_LEN,
                     poll_timeout_s=DEFAULT_POLL_TIMEOUT_S,
                     poll_interval_s=DEFAULT_POLL_INTERVAL_S,
                     shared_dir="/mnt/shared-workspace/shared",
                     workdir=None, log=print):
    """Fold a single-chain (MONOMER) `sequence` robustly; return a result dict.

    Tries the size-selected primary predictor first, then the fallback chain,
    each under a bounded poll timeout. Runs extract_plddt on the first success.
    Always writes <name>_run_manifest.json to out_dir. Never blocks on a callback.

    SCOPE: single chain only. Multi-chain complexes are NOT handled by this
    automated path — the per-method job builders emit single-chain inputs
    (AlphaFold `--model_preset=monomer_ptm`, one-chain Boltz YAML), so folding a
    complex here would silently produce a monomer. If `is_complex=True` (or a
    ':'-joined multi-chain string is passed) this raises NotImplementedError and
    points to the manual multimer path in references/methods_reference.md
    (AlphaFold-multimer / Boltz / Chai). This keeps behavior honest rather than
    silently mis-folding a complex.
    """
    # ---- reject complexes explicitly (never silently fold as a monomer) ----
    if is_complex or (isinstance(sequence, (list, tuple)) or ":" in str(sequence)):
        raise NotImplementedError(
            "orchestrate_fold handles single-chain monomers only. For a multi-chain "
            "complex, build the multimer job manually per references/methods_reference.md "
            "(AlphaFold-multimer: --model_preset=multimer with one FASTA record per chain "
            "copy; or Boltz/Chai with one block per chain) and extract with "
            "scripts/extract_plddt.py. The automated size/poll/fallback loop is "
            "monomer-only by design.")

    sequence = "".join(str(sequence).split()).upper()
    seq_len = len(sequence)
    os.makedirs(out_dir, exist_ok=True)
    if workdir is None:
        workdir = os.path.join("/workspace", f"fold_{name}")
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(shared_dir, exist_ok=True)

    primary, chain = choose_predictor(seq_len, methods=methods,
                                      esmfold_max_len=esmfold_max_len)
    order = [primary] + chain
    log(f"[orchestrate] {name}: len={seq_len} (monomer) "
        f"esmfold_max_len={esmfold_max_len}")
    log(f"[orchestrate] predictor order: {order}  (primary={primary})")

    hpc_run_tool, get_results, hpc_get_logs, hpc_cancel_job = _hpc()

    manifest = {
        "name": name, "sequence_length": seq_len, "scope": "monomer",
        "esmfold_max_len": esmfold_max_len,
        "poll_timeout_s": poll_timeout_s, "poll_interval_s": poll_interval_s,
        "requested_methods": methods, "predictor_order": order,
        "attempts": [], "fallback_trail": [],
        "chosen_predictor": None, "status": "no_success",
    }

    winner = None
    for method in order:
        log(f"\n[orchestrate] === trying {method} ===")
        att = {"method": method, "job_id": None, "status": None,
               "outcome": None, "seconds": None, "n_files": 0}
        t0 = time.time()
        try:
            tool_id, cmd, inputs = _build_job(method, name, sequence, workdir,
                                              esmfold_max_len)
            sub = hpc_run_tool(tool_id, cmd, input_files=inputs)
            job_id = sub.get("job_id")
            att["job_id"] = job_id
            att["submit_status"] = sub.get("status")
            log(f"    submitted {tool_id} job {job_id}")
            # persist job id immediately (survives context loss)
            _persist(shared_dir, name, manifest, att)

            outcome, res = _poll_until_done(get_results, job_id, poll_timeout_s,
                                            poll_interval_s, log=log)
            att["outcome"] = outcome
            att["status"] = str(res.get("status"))
            att["n_files"] = len(res.get("files") or [])
            att["seconds"] = round(time.time() - t0, 1)

            if outcome == "completed":
                out_sub = res.get("output_dir")
                att["output_dir"] = out_sub
                log(f"    {method} completed in {att['seconds']}s -> {out_sub}")
                winner = (method, out_sub)
                manifest["attempts"].append(att)
                break
            else:
                # stalled/failed/empty -> cancel if still running, then fall back
                log(f"    {method} outcome={outcome}; cancelling & falling back")
                if outcome in ("timeout", "empty"):
                    try:
                        hpc_cancel_job(job_id)
                        att["cancelled"] = True
                    except Exception as e:
                        att["cancel_error"] = repr(e)
                manifest["attempts"].append(att)
                manifest["fallback_trail"].append(
                    {"from": method, "reason": outcome})
        except Exception as e:
            att["outcome"] = "exception"
            att["error"] = repr(e)
            att["seconds"] = round(time.time() - t0, 1)
            log(f"    {method} raised: {e!r}; falling back")
            manifest["attempts"].append(att)
            manifest["fallback_trail"].append(
                {"from": method, "reason": "exception"})
        _persist(shared_dir, name, manifest, None)

    # ---- extract from the winner (if any) ----
    if winner is not None:
        method, out_sub = winner
        mkey = {"esmfold": "esmfold", "alphafold": "alphafold",
                "boltz": "boltz", "chai": "chai"}[method]
        prefix = os.path.join(out_dir, f"{name}_{mkey}")
        try:
            res = _xp.extract_plddt(mkey, out_sub)
            csv_path, png_path = _xp.write_outputs(res, prefix)
            manifest["chosen_predictor"] = method
            manifest["status"] = ("success_primary"
                                  if method == primary else "success_fallback")
            manifest.update({
                "mean_plddt": round(float(res["mean_plddt"]), 2),
                "ptm": (round(float(res["ptm"]), 3)
                        if res.get("ptm") is not None else None),
                "n_res": int(res["n_res"]),
                "selected_model": res.get("selected"),
                "structure": res.get("structure"),
                "csv": csv_path, "plot": png_path,
                # canonical band counts (from confidence_breakdown via
                # extract_plddt.write_outputs); None if that module was absent.
                "bands_csv": res.get("bands_csv"),
                "band_breakdown": res.get("band_breakdown"),
            })
            log(f"\n[orchestrate] WINNER={method} mean_plddt={manifest['mean_plddt']} "
                f"ptm={manifest['ptm']} n_res={manifest['n_res']}")
        except Exception as e:
            manifest["status"] = "success_but_extract_failed"
            manifest["extract_error"] = repr(e)
            log(f"[orchestrate] extract failed on {method}: {e!r}")
    else:
        log("[orchestrate] NO predictor succeeded within timeouts.")

    # ---- always write the manifest ----
    man_path = os.path.join(out_dir, f"{name}_run_manifest.json")
    with open(man_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    manifest["manifest_path"] = man_path
    log(f"[orchestrate] manifest -> {man_path}")
    return manifest


def _persist(shared_dir, name, manifest, latest_attempt):
    """Best-effort checkpoint of progress to shared workspace."""
    try:
        snap = dict(manifest)
        if latest_attempt is not None:
            snap = dict(manifest)
            snap["in_progress_attempt"] = latest_attempt
        with open(os.path.join(shared_dir, f"{name}_fold_jobs.json"), "w") as fh:
            json.dump(snap, fh, indent=2)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _read_seq(args):
    if args.seq:
        return "".join(args.seq.split())
    if args.fasta:
        seq = []
        for line in open(args.fasta):
            if not line.startswith(">"):
                seq.append(line.strip())
        return "".join(seq)
    raise SystemExit("provide --seq or --fasta")


def main():
    ap = argparse.ArgumentParser(description="Robust unattended protein folding "
                                             "(size-based default + poll-timeout + fallback).")
    ap.add_argument("--seq", help="raw amino-acid sequence")
    ap.add_argument("--fasta", help="path to a single-sequence FASTA")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True, help="output dir (e.g. /mnt/results)")
    ap.add_argument("--methods", nargs="*", default=None,
                    help="explicit method order override "
                         "(esmfold alphafold boltz chai). Omit for size default.")
    ap.add_argument("--complex", action="store_true", dest="is_complex",
                    help="multi-chain complex — NOT supported by this automated "
                         "monomer-only path; will raise with instructions for the "
                         "manual multimer route.")
    ap.add_argument("--esmfold-max-len", type=int, default=DEFAULT_ESMFOLD_MAX_LEN)
    ap.add_argument("--poll-timeout-s", type=int, default=DEFAULT_POLL_TIMEOUT_S)
    ap.add_argument("--poll-interval-s", type=int, default=DEFAULT_POLL_INTERVAL_S)
    a = ap.parse_args()
    seq = _read_seq(a)
    man = orchestrate_fold(
        seq, a.name, a.out, methods=a.methods, is_complex=a.is_complex,
        esmfold_max_len=a.esmfold_max_len, poll_timeout_s=a.poll_timeout_s,
        poll_interval_s=a.poll_interval_s)
    print(json.dumps({k: man[k] for k in (
        "chosen_predictor", "status", "mean_plddt", "ptm", "n_res",
        "structure", "csv", "plot", "manifest_path") if k in man}, indent=2))


if __name__ == "__main__":
    main()

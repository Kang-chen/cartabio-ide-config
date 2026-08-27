# Performance and caching

## Execution model

Keep the canonical run on coordinator-local POSIX storage:

```bash
RUN=/workspace/literature-deep-review/<run-id>
CHECKPOINT=/mnt/shared-workspace/shared/literature-deep-review/<run-id>
RESULTS=/mnt/results/literature-deep-review/<run-id>
```

Use up to five Biomni managed machines and one tracked background `Bash` job per
machine for acquisition, parsing, and figure OCR. Each job owns a paper queue
and an adaptive local process pool. It pilots real papers at 2, 3, 4, 6, 8, 12,
and at most 16 processes, subject to the number of papers remaining. The pilot
outputs are retained, so calibration does not throw work away.

Managed execution is mandatory for broad reviews and for deep reviews with at
least 12 selected papers. A platform exception requires the user's explicit
approval and a structured reason in `config.managed_execution_waiver`.
Reconciliation otherwise fails closed. The planner's adaptive machine count,
object-store exchange mode, package hash, and completed-machine inventory must
agree with `run_manifest.json` and `state/skill_provenance.json`. Record each
successful Biomni background submission with the `record-launch` subcommand of
`managed_machine_shards.py`; one receipt is required per completed machine.

`$CHECKPOINT/managed` is an object-store-safe courier. The coordinator publishes
immutable inputs and `skill.tar`, then writes `READY.json` last. Each worker
downloads inputs to local `/workspace`, computes there, and publishes one tar,
checksum manifest, and `DONE.json` under a unique task `attempt-N/` prefix.
The coordinator downloads and verifies every bundle before extracting and
merging under `$RUN`. Shared objects are never renamed, overwritten, appended,
or deleted. Never run parser caches, extraction, or merge state on the mount.

Prod has no general-purpose Biomni subagents. Managed machines execute Python
and shell code; they do not perform native evidence reasoning. Adjudication,
blinded entailment, narratives, synthesis, and report decisions remain in the
Biomni coordinator. `batch_tasks.py stage-workers` packs independent native
tasks into bounded coordinator turns. Packing reduces orchestration turns but
does not create concurrent reasoning agents. These packs live under
`$RUN/state/native_exchange`, not `$CHECKPOINT`, because their atomic assembly
and resume state require local POSIX semantics.

Coordinator compaction is not a state-transfer mechanism. The durable state is
`corpus/corpus_ledger.json`, the staged task inventory, per-task outputs, and
`state/assemblies/*.json`. Every assembly checks the original task hashes and
requires exactly one output per task before writing a canonical result. Resume
and synthesis read those artifacts, never a prose summary of worker results.
Adjudication receipts retain each batch's paper, claims, and examined blocks
even when zero anchors are accepted, so negative-search coverage also survives
compaction and flat-JSONL import.

Native packs are token/character bounded and adapt toward at most eight packs
per reasoning stage when task size permits. Adjudication defaults to four paper
batches per pack (rather than two); entailment defaults to eight anchors and
narratives to four claims, with the character ceiling taking precedence. Each
task still has its own output path and hash, so larger packs reduce coordinator
handoffs without merging scientific decisions or making compaction lossy.
Narrative tasks are sorted by evidence axis before packing, keeping related
claims together without allowing one claim's output to serve as another's.
Figure visual-entailment checks are independent Biomni `Read` calls and may run
while narrative packs are being completed; export waits for the assembled,
image-hashed verdict ledger.

## Adaptive process selection

`managed_machine_shards.py prepare` distributes complete paper queues over at
most five machines. It requests approximately one machine per 8 selected papers
for OCR-all, 12 for targeted OCR, or 16 with OCR off, rounded up and capped at
five. Each `run-machine` job:

1. detects the machine's logical CPU count and currently available memory;
2. applies a conservative initial memory estimate and a 2× logical-CPU ceiling;
3. runs real one-paper pilots at increasing concurrency;
4. recalculates the memory ceiling from measured peak worker RSS;
5. keeps increasing only while throughput improves by at least 5%; and
6. stops at the preceding healthy level when memory pressure, remaining
   transient retrieval failures, excessive retries, or a child-process failure
   appears.

A failed parallel child is retried serially with its existing local cache.
Transient acquisition is retried by `evidence_first.py` and, when still
unresolved, the task is automatically rerun once before it is finalized. A
retrieval-pressure signal stops further concurrency increases; it does not
silently narrow the corpus.

The launch-plan identity includes the corpus, claims, process ceiling, review
mode, and OCR mode. `run-machine` refuses mode or OCR drift instead of silently
reusing incompatible cached output.

Machine `completion-attempt-*/machine_completion.json` records
`selected_processes`, `stop_reason`, detected
resources, every pilot wave, measured throughput, peak worker RSS, retrieval
pressure, and task timings. `merge` refuses incomplete machine sets, restores
original paper order, rewrites local artifact paths, and stores the adaptive
metrics in `run_manifest.json`.
`merge` also verifies each task's exact paper IDs, refuses duplicate/missing
outcomes, requires one parsed artifact per retrieved paper, and refreshes the
canonical corpus ledger.

Before either renderer and again before delivery,
`scripts/reconcile_run.py` derives paper, claim, evidence, entailment, OCR, and
figure counters from canonical artifacts. It stores hashes in
`state/final_reconciliation.json`, requires managed-machine metrics when
adaptive execution was configured, verifies every native assembly receipt
against the current destination bytes, and records native stage time from task
staging through deterministic assembly. Calendar span from resumable run
timestamps is labeled separately from active stage timing.
Reconciliation writes are content-idempotent. Search freshness depends on the
immutable `state/intake_snapshot.json`, not on mutable manifest metrics.
Finalization runs reconciliation, preflight, verified copy, and attestation in
one tracked background command so an interactive tool cutoff cannot strand a
fully built report on coordinator-local disk.

## Where time goes

| Stage | Parallel path | Limit |
|---|---|---:|
| acquire + parse + targeted figure OCR | adaptive managed-machine queues | up to 5 machines × measured safe process count (hard cap 16 each) |
| native adjudication/entailment/narratives | adaptive bounded native task packs | one Biomni coordinator; separate output per task |
| claim/figure visual and crop checks | independent Biomni Read tasks | overlap with narrative work where the tool scheduler permits |
| synthesis, build, gates, delivery | Biomni coordinator | serial and comparatively short |

## Expected speedup

Do not infer end-to-end speedup from the maximum process count. Network
waterfalls, publisher rate limits, machine provisioning, PDF difficulty, OCR,
and coordinator reasoning impose separate ceilings. The measurable model is:

```text
new runtime = native coordinator time
            + adaptive deterministic critical path
            + provisioning and merge overhead
```

For a 65-paper corpus split over five machines, each machine receives about 13
papers. That is enough to pilot 2, then 3, then 4 processes, but generally not
enough to reach 6 because pilots retain and consume papers. Larger corpora can
test higher levels. Thus the relevant expectation for the reported 65-paper
case is commonly up to four processes per machine if resources and source
services support it—not the hard cap of 16.

Relative to the previous fixed 5×2 design, selecting four processes per machine
can approach a further 2× improvement only for the deterministic stage. If
that stage was 40 minutes of a two-hour run, an ideal halving saves about 20
minutes; if it was 15 minutes, the maximum useful saving is about 7–8 minutes.
Native task packing may save additional coordinator handoff time, but its effect
must be measured rather than assumed. Record a runtime estimate only after
comparing `critical_path_seconds` and coordinator stage timings on the same
frozen corpus.

## Launch discipline

1. Require `READY.json`, then create only the machines listed in the verified
   `launch_plan.json`.
2. Copy the single shared `skill.tar` to each machine, verify its plan-recorded
   SHA-256, and extract it locally. Run local `scripts/install.sh` once. It imports EasyOCR and
   PyTorch first and installs them only if absent.
3. Submit one `run-machine` call per machine with a distinct `background_name`
   and `run_in_background=true`. Python manages the adaptive subprocesses;
   never use shell `&` or `nohup`.
4. Wait for Biomni completion callbacks. Do not poll.
5. Inspect each `completion-attempt-*/machine_completion.json`. A low selected count is valid only when its
   recorded stop reason and wave metrics explain it.
6. Merge only after every machine completion marker exists.
7. Read `fulltext/global_transient_retry.json`. If incomplete, use
   `prepare-retry` to launch a second managed wave over only merged
   `retrieval_failed` records; merge it before synthesis. Confirmed paywalls
   are never placed in this wave.
8. Resume `evidence_first.py` with `--preprocessed-run` so the coordinator does
   not redownload or reparse the corpus.
9. For each reasoning stage, process the emitted files under
   `$RUN/state/native_exchange/native_packs/<kind>/`; every task must still write its own
   output. Re-staging preserves an existing output only when its source-task
   fingerprint is unchanged, so resume does not repeat completed reasoning.

The planner automatically uses fewer machines for smaller corpora. Do not
force additional machines or processes when the adaptive runner reports
retrieval or memory pressure.

## Acquisition and parse recovery

Successful parses live under `fulltext/parsed/` and `cache/parsed/`. Acquisition
memoization lives at `fulltext/pdfs/_acquire_cache.json`.

- confirmed closed/paywalled results retain the long negative TTL;
- transient retrieval failures are retried automatically with the negative
  cache bypassed;
- remaining transient misses are recorded separately from paywalls in
  `fulltext/not_retrieved.jsonl` and the run metrics;
- the post-merge global recovery result is recorded in
  `fulltext/global_transient_retry.json` and is a finalization gate;
- `--refresh-acquisition` forces the full retrieval waterfall;
- `--fast-fail-closed` is an explicit recall-for-speed tradeoff, never default.

Retrieval success is not automatically parse success. The pipeline records one
route receipt per selected paper and one quality receipt per retrieved paper.
A zero-body JATS result is retried from an available paper/figures PDF; the
richer parse wins. High-quality parser failure is recorded and the primary
parse survives. `figure_only` remains available for figure selection but does
not count as substantive body text, while `unusable` blocks final delivery.

Europe PMC's REST full-text XML and NCBI OA endpoints precede the interactive
Europe PMC `?pdf=render` fallback. When the OA-subset service has no PDF, the
waterfall also reads the official public PMC article page and follows only its
declared `citation_pdf_url`; this recovers freely readable NIH author
manuscripts without treating them as openly licensed. The corpus never guesses
a protected endpoint. HTTP 429
and transient 5xx responses honor numeric `Retry-After`, add randomized jitter,
and feed the existing retrieval-pressure signal that stops concurrency growth.
All Europe PMC request starts on one managed machine also share a process-safe
pacer (`EPMC_MIN_INTERVAL_S`, default 0.5 seconds), preventing a large local
worker pool from emitting a synchronized burst.
Use a stable truthful contact address (`PHYLO_LITERATURE_CONTACT_EMAIL`) and
optional descriptive `LITERATURE_HTTP_USER_AGENT`; do not rotate identity or IP
addresses to evade a provider limit. Multiple managed machines may share one
NAT egress IP, so retrieval pressure is a reason to reduce aggregate
concurrency and run the bounded recovery wave, not to add more machines.

Targeted OCR includes image-backed captionless crops from candidate papers.
After initial selection, uncovered evidence axes trigger a second bounded OCR
pass over captionless crops from those axes' cited papers. Figure selection can
match captions or OCR text and reports unique crops, claim–figure pairs, source
papers, roles, and per-axis coverage separately.
OCR lineage is explicit per crop. `ocr=all` fails on an unattempted or failed
image-backed crop instead of treating an absent/empty OCR array as success.

## Diagnosing a slow run

1. Separate adaptive acquisition/parse/OCR time from coordinator reasoning
   time.
2. Inspect each machine's `selected_processes`, `stop_reason`, and wave
   throughput.
3. Check peak worker RSS and the minimum available memory before raising the
   hard cap.
4. Check transient retries, recovered papers, remaining failures, and source
   rate-limit pressure.
5. Confirm one tracked background job was submitted on every planned machine.
6. Confirm the canonical resume used `--preprocessed-run`.
7. Check cache-hit counts and parser-version changes.
8. Re-run only failed or stale stages; `run_state.py --show` prints the next
   action.
9. Use `state/quality_summary.json` to compare active invocation seconds,
   machine execution seconds, cache hits, parse states, and terminal retrieval
   outcomes. Resumed calendar span is not runtime, and invocation timing must
   accumulate rather than overwrite earlier work.

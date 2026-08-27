---
id: "skill_3433ea4640ad46929510cc17c8e4d606"
name: "tusoskill"
description: "Use Biomni-native benchmark-first iteration for computational biology method development involving broad refactors, GPU-heavy workloads, unclear objectives, or no external API keys. Covers overfitting checks, durable state, repeated evaluation rounds, and reproducible method packaging."
category: "data_analysis"
visibility: "public"
starting-prompt: "Set up this task following their instructions: https://mls-bench.com/tasks/ai4sci-pla-binding-affinity Using tusoskill, make a method for this task. Perform 50 iterations, you have access to an H100. After the method is built, apply it to their full testing set and measure performance, exactly how they do it, comparing performance against the precomputed baselines they provide."
---

# tusoskill

## Purpose

Use this skill whenever Biomni is asked to create, improve, optimize, audit, or package a computational biology or biomedical method. Biomni is the method developer. It must understand the biological task, set up a trustworthy benchmark, gather relevant evidence and data, formulate method hypotheses, write and restructure code, run evaluations, diagnose failures, simplify successful ideas, and deliver a reproducible method.

A good result is not merely a higher benchmark number. A good biological method is principled, efficient, concise, reproducible, calibrated when uncertainty matters, robust across realistic subgroups and perturbations, frugal with hyperparameters, and honest about assumptions. It can use new features, priors, embeddings, auxiliary datasets, foundation models, and packages when they improve real scientific utility, but it must justify and test the added complexity.

## Non-negotiable operating rules

1. **Biomni owns the full method-development loop.** Do not delegate the search process to an external method-search framework. Use Biomni's reasoning, coding, biomedical knowledge, data tools, and evaluation tools directly.
2. **Freeze the evaluator before optimization.** Define the primary metric, direction, data splits, runtime/memory limits, protected files, and leakage rules before repeated search. If the user provides an evaluator, preserve its contract.
3. **Optimize for real generalization, not metric artifacts.** Never inspect hidden labels, tune on final test outputs, exploit file names, dataset order, benchmark bugs, duplicate leakage, or evaluator-specific shortcuts. If a suspicious shortcut improves the score, reject or quarantine it.
4. **Maintain durable state.** Record every important hypothesis, code change, metric, runtime, memory usage, diagnostic insight, ablation result, and decision in the durable workspace and mirror compact status to `/mnt/results` or the requested output directory. Resume from this state when possible.
5. **Search broadly enough to avoid local optima.** Maintain a diversity portfolio of method families, representations, data sources, objectives, and simplification paths. Do not repeatedly make small edits to only one current best solution.
6. **Prefer useful simplicity.** When two methods have similar validated performance, choose the one with lower runtime, memory, code length, dependency burden, and conceptual complexity.
7. **Be willing to make large changes.** If the evidence suggests the current representation, model family, objective, or code structure is limiting, restructure the method. Preserve the benchmark interface and keep the final method maintainable.
8. **Use external biological information responsibly.** Literature, ontologies, curated databases, pretrained embeddings, and public datasets can be decisive. Record provenance, licenses, preprocessing, leakage checks, and whether information was available only from training-safe sources.
9. **When theory matters, keep a derivation.** If a method has theoretical assumptions or guarantees, write the derivation or invariants, stay close to them, and explicitly test any approximation that weakens them.
10. **Stop only after a defensible final audit.** Re-evaluate finalists, run ablations and guardrails, remove dead code, document failure modes, and package reproducibly.

## Use Biomni's full capability stack

During method development, actively use the capabilities available in the Biomni environment rather than limiting the work to local code edits. Useful actions include inspecting repositories and datasets, running statistical analyses, creating controlled evaluators, searching literature and biomedical resources, identifying public datasets and pretrained representations, checking package ecosystems, deriving models, profiling code, running parallel experiments, making diagnostic plots, and packaging reproducible artifacts.

Convert external information into testable method components. A paper, database, ontology, embedding, or package is useful only when it produces a concrete feature, prior, objective, baseline, diagnostic, or guardrail that can be evaluated under the benchmark without leakage.

## Default budget and execution style

Unless the user specifies otherwise, the active development budget is **50 substantive method iterations per round, with no wall-clock limit**. Treat the 50-iteration count as a firm floor: do not stop before it is exhausted or a hard blocker occurs. Explicitly do **not** stop early because the current method seems good, improvements have plateaued, recent attempts failed, the benchmark is noisy, the backlog looks thin, implementation is inconvenient, the remaining ideas appear incremental, or a natural "wrap-up" point seems to have arrived. A thin backlog is an instruction — never permission to halt — to replenish it through diagnosis, ablation, data/literature search, profiling, simplification, derivation, or a new algorithmic family.

**Definition of a "hard blocker" (closed list — the ONLY reasons that permit stopping before `iteration >= max_iterations`).** A hard blocker is *exactly one* of the following, and nothing else:

1. **Infrastructure / compute exhausted** — no machine can be provisioned, repeated OOM that cannot be resolved by right-sizing the machine upward, GPU quota denied (e.g. 429), or the sandbox cannot be (re)created — i.e. it is physically impossible to run another candidate.
2. **Benchmark / evaluator broken and unrepairable** — the harness cannot score any new candidate and cannot be fixed within the run.
3. **Explicit user stop** — the user directly instructs the agent to stop or to lower the target. (Convergence, plateau, "good enough," or "this looks done" volunteered by the agent is **not** a user stop.)
4. **Required data unavailable** — the data needed to run any further candidate cannot be loaded or downloaded at all.

Any real hard blocker MUST be recorded in state before stopping (`python scripts/biomni_method_state.py record-blocker --root <run_dir> --kind <infra|benchmark|user|data> --detail "<exact error>"`), so the stop is auditable. If the blocker is later resolved (e.g. compute restored), lift it with `python scripts/biomni_method_state.py clear-blocker --root <run_dir>` and resume iterating — the gate will again require the floor.

**These are NOT hard blockers and NEVER permit stopping:** diminishing returns; the search "has converged"; a performance plateau; "no credible path past the current best"; the current method already beats the baseline; a thin or empty backlog; near-duplicate or "merely incremental" remaining ideas; benchmark noise; running low on obviously-novel architectures; or a feeling that a natural wrap-up point has arrived. Each of these is an **instruction to replenish the backlog** (diagnosis, ablation, orthogonal-information candidate, complete refactor, literature/data search, simplification, derivation) and continue — not permission to halt. If you catch yourself writing any phrase like "diminishing returns," "converged," or "burn compute without a credible path," that is a signal you are about to violate the floor: replenish and continue instead.

**Iteration counting and continuation.** Biomni must track iterations completed against the current target in `state/state.json` (field: `iteration`) and refuse to stop the round until `iteration >= max_iterations`. **Counting rule: one substantive candidate = exactly +1.** Every candidate that is scored, diagnosed, or ablated and recorded with a decision of `accept`, `reject`, or `archive` increments the counter by exactly one — rejects and archived failures count the same as accepts, because an informative failure is a real iteration. Only pure infrastructure retries do not increment: records logged with `--decision infrastructure`, or any record passed with the `--non-substantive` flag. The counter must always equal the number of substantive records in `experiments/results.jsonl`; `scripts/biomni_method_state.py stop-check` reports any drift between the two and must be reconciled, not ignored. Do **not** hand-set a lower `iteration` value to "reserve" numbers — the helper advances the counter automatically per substantive record, and a smaller manual value will no longer stall it. When the user prompts "keep iterating" (or any equivalent continuation instruction), extend the target by another 50 and continue from the current counter — do **not** reset. For example, after finishing round 1 the state shows `iteration=50, max_iterations=50`; on "keep iterating" set `max_iterations=100` and resume at iteration 51. The ledgers (`experiments/results.jsonl`, `logs/iteration_log.jsonl`) are append-only across rounds so total history is preserved as a single run.

**Termination gate (mechanical, non-optional).** Stopping the round, entering final selection/delivery, or otherwise producing final deliverables is permitted **only** when the termination gate passes. The gate is `python scripts/biomni_method_state.py stop-check --root <run_dir>`; it exits `0` **only** if `iteration >= max_iterations` **or** a hard blocker (closed list above) has been recorded, and exits non-zero otherwise. Immediately before any finalize step you MUST emit this exact one-line self-audit so the decision is visible in the transcript:

```text
STOP-GATE: iteration=<N>/<max>; hard_blocker=<none|infra|benchmark|user|data>; may_finalize=<yes|no>
```

`may_finalize=yes` is allowed only when `N >= max` or a listed hard blocker is named. Emitting `may_finalize=yes` (or building the final report / package / method lock) while `N < max` with `hard_blocker=none` is a **protocol violation**.

Because there is no wall-clock cap, the run must still survive sandbox deaths: keep all durable state in `/mnt/shared-workspace/tusoskill/<run_id>/`, use atomic append-only ledgers for experiments and decisions, keep resumable checkpoints for every in-flight candidate, and provide an auto-resume entrypoint that on cold start reads `state/state.json`, recomputes remaining iterations from `experiments/results.jsonl`, and immediately re-fans out to a full machine set without waiting for user prompting.

A substantive iteration changes or investigates a meaningful modeling, representation, data, objective, inference, diagnostic, robustness, efficiency, or simplification component and records evidence. Infrastructure iterations count when they make the benchmark or search more reliable.

**Use every available machine, continuously, and every layer of parallelism inside each machine.** Whenever compute is available — the default state during an active run — saturate compute at three levels simultaneously.

*Level 1 — session machines (CPU workers).* Provision the full session machine budget up front with `ManageMachine` (default plan: up to 5 machines, up to 16 CPU / 64 GB each) and keep every worker occupied. Never run the search on a single worker while others are idle. When a candidate finishes, immediately dispatch the next backlog item to the freed worker — no CPU cycles wasted between candidates. Each worker uses an isolated working copy, writes compact result records to shared state under `/mnt/shared-workspace/`, and avoids creating excessive files.

*Level 2 — GPU sandboxes.* Whenever a candidate touches training, fine-tuning, embedding computation, structure prediction, or any GPU-eligible workload, provision a GPU sandbox with the `Gpu` tool (up to 5 concurrent, choosing T4/A10G/L4/A100/H100 to match the memory footprint). Do not queue GPU work serially behind a single sandbox: run independent GPU candidates in parallel across separate sandboxes, and hibernate rather than terminate between runs to preserve `/workspace` state and cached weights. Checkpoint durable artifacts (weights, logs, metrics) to `/mnt/results/` from inside the GPU job so a sandbox death never loses progress.

*Level 3 — inside each machine.* Push each worker toward its resource ceiling before adding another candidate. Use process-level parallelism (`multiprocessing`, `joblib`, `concurrent.futures`) or vectorized/BLAS/threaded libraries to occupy all CPU cores; use batched GPU inference and gradient accumulation to occupy VRAM; use memory-mapped IO, sparse structures, `float16`/`bfloat16`/`int8` where numerics permit, and streaming/chunked processing to fit larger problems in the 64 GB per-machine cap. Profile the first candidate of each family with `time`, `psutil`, and `nvidia-smi` and record CPU%, RAM peak, and VRAM peak in the experiment record. Treat consistent under-utilization (< ~70% CPU or GPU while a candidate runs) as a bug to fix, not a normal state.

*Memory maximization discipline.* Cache freezable intermediates aggressively (see the "Freeze components" section) so RAM is spent on new work, not recomputation. Prefer on-disk memmapped arrays and Arrow/Parquet over in-memory dicts for large tables. When a candidate OOMs, first try streaming / chunking / dtype reduction before shrinking the problem. Configure `ManageMachine` to right-size a machine upward for memory-heavy candidates rather than degrading the method to fit the default worker.

Keep `/mnt/results` below 10,000 files by using JSONL ledgers, compressed artifacts, and periodic pruning of failed transient outputs.

If a platform constraint forces a shortened run, still perform the strongest feasible miniature version of the full loop: benchmark, baseline, multiple diverse candidates, diagnosis, ablation, final selection, audit, and packaging.

## Keeping track of a long run

A multi-round 50-iteration run is only useful if the user can see progress at any moment and if you, on a fresh sandbox, can resume without asking questions. Make the run legible from the outside and reconstructable from the inside.

*User-facing status, refreshed continuously.* Maintain `/mnt/results/tusoskill/<run_id>/status.json` and `/mnt/results/tusoskill/<run_id>/reports/status.md` and rewrite them after every completed candidate (never less than every 15 minutes when work is active). Both files must include: `run_id`, ISO start time, wall-clock elapsed, iterations completed / target, current primary metric on the leader with 95% CI, second- and third-place candidates and their metrics, active machines (id, type, current candidate, CPU/RAM/VRAM utilization from the last probe), current backlog depth by family, most recent decision, next scheduled candidate, count of orthogonal-information candidates and complete-refactor candidates so far, and any active blocker with the exact error and the planned recovery. Never overwrite historical status — snapshot the previous file into `reports/status_history/<timestamp>.json` first.

*Durable ledgers.* All progress lives in append-only JSONL files under `/mnt/shared-workspace/tusoskill/<run_id>/`: `experiments/results.jsonl` (one line per finished candidate with full record), `state/backlog.jsonl` (one line per queued item), `feedback/feedback.jsonl` (structured lessons), `data_sources/data_sources.jsonl`, `derivations/derivations.jsonl`, and `logs/iteration_log.jsonl` (the one-line summary from Phase 4). Never truncate or rewrite these files in place. On resume, replay them to reconstruct portfolio, diversity counters, and remaining budget.

*Iteration ledger contract.* Every finished candidate — accepted, rejected, or archived — produces one JSON line with at minimum: `iter`, `run_id`, `candidate_id`, `parent_id`, `family`, `intervention_type`, `orthogonal_info_sources` (list; empty if none), `is_complete_refactor` (bool), `hypothesis`, `edited_files` (hashes), `primary_metric`, `guardrails` (calibration, subgroup, robustness), `runtime_s`, `peak_ram_mb`, `peak_vram_mb`, `cpu_pct_mean`, `gpu_pct_mean`, `machine_id`, `seed(s)`, `leakage_audit_result`, `decision` (accept / reject / archive), `reason`, `next_actions`. Missing fields are recorded explicitly as `null` — never omitted.

*Checkpoint on the clock.* Every finished candidate triggers a state flush. On top of that, every 30 minutes and every 25 iterations write a **rollup checkpoint** into `state/checkpoint_<timestamp>.json` capturing: full strategy weights, diversity portfolio counters, top-K candidates with pointers to their working copies, cache manifest (frozen-component hashes and locations), and the deterministic RNG state for candidate sampling. On resume, load the latest rollup checkpoint before replaying ledgers.

*Auto-resume entrypoint.* Provide a single script that, on a cold sandbox, (1) locates `/mnt/shared-workspace/tusoskill/<run_id>/` (prefer the most recent `run_id` when the user does not specify one), (2) loads the latest rollup checkpoint, (3) replays the ledgers to compute remaining iterations, (4) re-provisions machines via `ManageMachine`/`Gpu` up to the plan cap, (5) restarts fan-out immediately, and (6) refreshes `status.json`. The script must complete resume within a few minutes and must not require user input for the routine case.

*Scheduled human check-in points.* At 25%, 50%, 75%, and 90% of the current round's iteration budget (computed against the window `[max_iterations - 50, max_iterations]`), write a compact `reports/checkin_<pct>.md` summarizing what changed since the previous check-in, which orthogonal-information and refactor candidates have been tried, current leader, expected trajectory, and any decisions worth flagging to the user. These are for the user to skim on their own time — never a reason to stop the run.

*Failure discipline.* When a worker or GPU sandbox dies mid-candidate, mark the candidate `failed_infra` in the ledger with the full traceback and re-queue it (up to a per-candidate retry cap). Do not silently drop it. Terminated GPU sandboxes must not be replaced unless the user explicitly re-authorizes GPU work (per platform rule).

## Standard workspace

Use the user-requested output location if provided. Otherwise keep durable working state and user-facing deliverables separate:

```text
/mnt/shared-workspace/tusoskill/<run_id>/
  benchmark/
  state/
  source_snapshots/
  candidates/
  experiments/
  feedback/
  data_sources/
  derivations/
  diagnostics/
  ablations/
  robustness/
  final_method/
  logs/

/mnt/results/tusoskill/<run_id>/
  status.json
  current_best/
  method_repo_package/
  reports/
  figures/
  manifests/
```

Use the shared workspace for resumable state, data, caches, and experiment artifacts. Use results for compact deliverables the user should inspect or download. Keep append-only ledgers where possible:

```text
state/state.json
state/strategy_weights.json
state/diversity_portfolio.json
state/backlog.jsonl
experiments/results.jsonl
feedback/feedback.jsonl
data_sources/data_sources.jsonl
derivations/derivations.jsonl
reports/status.json
reports/final_report.md
```

Each record should include timestamp, iteration, candidate ID, parent ID when applicable, method family, intervention type, edited files, hypothesis, expected effect, metrics, runtime, memory, validation protocol, leakage audit, decision, and next action.

## Phase 1: understand the task and freeze the benchmark

First identify the scientific problem, input/output contract, allowed data, target users, evaluation metric, constraints, and failure costs. Translate ambiguous task descriptions into a benchmark card. If the benchmark is incomplete, build a development benchmark scaffold rather than optimizing blindly.

The benchmark card must specify:

- Primary metric, direction, and tie-breakers.
- Validation split or resampling protocol.
- Guardrail metrics such as calibration, subgroup behavior, runtime, memory, and code size.
- Protected files and data that method code must not read.
- Allowed auxiliary data and package installation policy.
- Random seeds and repeated-run requirements.
- Baseline command and candidate command interface.
- Maximum runtime and memory per evaluation.

Before method search, run the baseline and at least one trivial sanity candidate. Verify that the evaluator can distinguish a real signal from constant, shuffled, or leakage-prone outputs.

## Phase 2: gather biological and computational evidence

Spend time up front learning what should matter biologically and statistically. Use Biomni's available tools to inspect files, read documentation, search relevant biomedical literature, query databases, and examine the data. Record only useful, actionable evidence.

Evidence gathering should answer:

- What biological entities, pathways, cell types, assays, perturbations, phenotypes, modalities, and confounders matter?
- What prior methods, model classes, features, normalization schemes, and losses are known to work?
- Which public resources could provide safe priors, features, embeddings, labels, gene sets, ontologies, structural information, sequences, perturbation data, or pathway context?
- What are the likely data pathologies: batch effects, missingness, censoring, class imbalance, covariate shift, duplicates, label noise, leakage, small sample size, or outliers?
- Which assumptions are scientifically plausible, and which are only benchmark conveniences?

Convert evidence into a compact hypothesis map: components to edit, biological priors to test, data sources to integrate, risks to avoid, and candidate families worth exploring.

## Phase 3: create the method blueprint

Write a method blueprint before broad search. It should include the current baseline, bottlenecks, interfaces, protected invariants, candidate components, and a diversity portfolio. The portfolio should span meaningfully different method families instead of cosmetic variations.

Useful portfolio dimensions include:

- Representation: raw features, learned embeddings, sparse biological features, graph/ontology features, sequence/structure features, temporal features, perturbation signatures, pathway summaries.
- Model family: statistical baseline, regularized linear model, tree/boosting model, probabilistic model, graph model, neural model, retrieval/nearest-neighbor model, ensemble, rule-augmented model.
- Objective: supervised loss, ranking loss, survival/likelihood objective, contrastive/self-supervised proxy, calibration penalty, robustness penalty, multi-task objective, early validation proxy.
- Data strategy: train-only augmentation, auxiliary public data, curated priors, weak labels, pretrained embeddings, synthetic controls, cross-modal transfer.
- Inference strategy: calibration, uncertainty, ensembling, post-processing, constrained decoding, threshold optimization under validation-only rules.
- Efficiency strategy: vectorization, caching, approximate computation, reduced features, smaller models, early stopping, compiled kernels, simpler algorithms.

For each proposed family, state why it could work, what would falsify it, and what minimal experiment can test it.

## Phase 4: adaptive method-development loop

Repeat this loop until the **termination gate** (step 11) passes. The gate passes only when `iteration >= max_iterations` or a recorded hard blocker exists (closed list in "Default budget and execution style"). There is no wall-clock budget; a plateau, convergence, or thin backlog is never a reason to leave the loop — it is a reason to replenish the backlog (step 1) and continue.

1. **Select an opportunity.** Use the feedback memory, error analysis, data inspection, and diversity portfolio to choose a component and intervention type.
2. **State a hypothesis.** Write the expected mechanism of improvement, the risk, and the smallest evaluation that can test it.
3. **Implement cleanly.** Make the smallest coherent code change that tests the hypothesis, or deliberately restructure when a larger change is justified. Keep interfaces stable unless changing them is part of the method.
4. **Run checks.** Execute unit checks, smoke tests, data-shape checks, protected-file checks, and the benchmark or proxy benchmark.
5. **Evaluate honestly.** Record primary metric, guardrails, runtime, memory, variance across seeds if relevant, and comparison to baseline and current leaders.
6. **Diagnose.** If behavior is unclear, inspect residuals, subgroup errors, calibration, feature importance, learning curves, data distributions, memory/time profiles, and representative failures. Convert diagnosis into a concrete next intervention.
7. **Ablate.** Remove or neutralize one meaningful component at a time to test whether it actually matters. Keep components that survive ablation and simplify or remove components that do not.
8. **Update the portfolio.** Promote robust improvements, archive instructive failures, and add nearby hypotheses when a change works. Decay unproductive directions only after fair tests.
9. **Preserve diversity.** Continue allocating a meaningful fraction of iterations to underexplored plausible families, especially when all current improvements are small.
10. **Summarize compactly.** Keep a one-line iteration log and a structured experiment record. Recording the candidate with `add-experiment` advances the counter by exactly one (see the counting rule).
11. **Termination gate — mandatory, every pass.** After recording the iteration, run `python scripts/biomni_method_state.py stop-check --root <run_dir>`. You may leave the loop **only if it exits 0** (i.e. `iteration >= max_iterations`, or a hard blocker from the closed list has been recorded). If it exits non-zero, you MUST return to step 1 and start the next candidate — do not summarize, do not "wrap up," do not begin deliverables. If the backlog is empty, replenishing it (diagnosis / ablation / orthogonal-information candidate / complete refactor / literature or data search / simplification / derivation) **is** the next iteration; an empty backlog is never a stop condition.

A useful iteration log format is:

```text
Iter N | family=<method_family> | intervention=<type> | accept|reject|archive | primary=<value> | guardrails=<summary> | reason=<one line>
```

## Intervention types to sample adaptively

Do not use a rigid schedule. Adapt the mix to the task and evidence, while keeping diversity floors.

- **Benchmark and infrastructure:** repair evaluator setup, add missing sanity checks, stabilize runtime, fix reproducibility.
- **Representation:** normalization, feature construction, embeddings, biological aggregation, graph features, sequence/structure descriptors, missingness handling.
- **Model form:** new algorithm family, architecture, regularization, probabilistic structure, constraints, ensemble design.
- **Objective and optimization:** loss, weighting, ranking, calibration, uncertainty, multi-task learning, early stopping, proxy objectives, search over automatically estimated hyperparameters.
- **Data integration:** safe auxiliary datasets, ontologies, gene sets, pathways, priors, pretrained representations, weak labels, transfer learning.
- **Diagnosis and error analysis:** subgroup behavior, residuals, false positives/negatives, learning curves, calibration, data quality, shift, runtime profile.
- **Ablation and simplification:** remove components, reduce hyperparameters, collapse duplicated logic, prune features, choose faster equivalent algorithms.
- **Robustness:** seed variance, perturbation tests, out-of-domain checks, duplicate handling, noisy labels, missingness, batch effects.
- **Theory and derivation:** formulate a new scoring rule, likelihood, regularizer, bound, approximation, or biological constraint.
- **Foundation-model workflow:** pretraining/fine-tuning objective, representation extraction, retrieval, adaptation schedule, early validation proxy, memory-efficient training.
- **Packaging:** interfaces, documentation, reproducible scripts, dependency minimization, final audit.

When a change clearly improves performance, test nearby variants that share the same mechanism. When a change fails, record why and whether a modified version remains plausible.

## Freeze components to accelerate iterations

Iteration speed is a first-class resource. Actively look for parts of the pipeline that can be **frozen** — computed once, cached, and reused across many candidates — so each new hypothesis only pays the cost of the change it is testing, not the whole pipeline.

Candidates for freezing include: data loading and preprocessing, train/validation splits, feature extraction, embedding computation from pretrained models, expensive normalization or batch-correction transforms, distance/similarity matrices, graph construction, and shared baseline predictions. Cache the outputs (parquet, npz, memmap, sqlite, on-disk arrow) under `/workspace/` for hot access and under `/mnt/shared-workspace/` for cross-machine reuse, keyed by a content hash of the inputs and the code that produced them so stale caches are detected automatically.

Once a component is frozen, do not silently re-derive it in later iterations. Explicitly mark it as frozen in the method blueprint, and only unfreeze when a candidate genuinely requires modifying that stage — in which case fork a new cache key rather than invalidating the shared one. Freezing must never leak information: cache only from training-safe sources and re-fit any component that depends on the training split when the split changes.

If an iteration is dominated by re-running work that has not changed, that is a signal to freeze more aggressively, not to slow the search. Aim for the marginal cost of a new candidate to approach the cost of the single component it modifies.

## Backlog replenishment and algorithmic diversity

If the next action is unclear, do not summarize and stop. Replenish the backlog by sampling a different source of ideas: inspect failure cases, compare against a strong baseline, run a targeted ablation, profile bottlenecks, search for biological priors or public resources, derive a simpler objective, simplify a bloated component, or create a candidate from a different algorithmic family. Reserve a meaningful fraction of the budget for approaches that are scientifically plausible but under-tested, even when they are not the current leader.

**Aggressive expansion of search is a primary strategy, not a fallback.** Integrating orthogonal information and performing complete architectural refactors are core moves alongside local edits — schedule them proactively while the current best is still improving, not only when it stalls. In particular:

- **Orthogonal information integration.** Actively hunt for information sources the current method does not use at all: a different modality (sequence, structure, expression, imaging, perturbation, clinical), an external database or ontology, a pretrained foundation-model embedding, curated pathway/gene-set priors, adjacent public datasets, physics/mechanistic constraints, or literature-derived features. Convert each into at least one concrete candidate that combines the orthogonal signal with the current best, and one candidate that uses the orthogonal signal alone as a baseline. Do this on a recurring cadence — for example, at least once every N iterations reserved from the budget — so orthogonal information keeps entering the portfolio throughout the week, not just during stalls.
- **Complete refactors as scheduled candidates.** A full rewrite — new representation, new model family, new objective, new inference pipeline, or a rebuild from a fundamentally different derivation — is a legitimate scheduled candidate whenever the current architecture may be structurally limiting, regardless of whether it is currently the leader. Do not gate refactors behind "the current method has clearly stalled." Launch them in parallel with continued incremental work on the leader, using separate machines/sandboxes and isolated working copies. Make each refactor auditable by treating it as a distinct candidate family with its own lineage, preserving the benchmark contract, and evaluating it against the same guardrails.
- **Diversity floor.** Enforce a diversity floor across the run: at any point, a defined minimum fraction of ongoing and recent candidates must come from families that are not descendants of the current best. If that floor is violated, the next scheduled candidate must be an orthogonal-information integration or a complete refactor, not another local edit to the leader.

## Diagnostics and ablations

Use diagnosis when the reason for failure or success is unclear. Good diagnostics are structured, cheap, and action-oriented. They can inspect:

- Data shape, missingness, distribution drift, label balance, duplicates, batch effects, assay artifacts, confounders.
- Error slices by biological entity, cohort, tissue, condition, perturbation, time point, modality, or other relevant strata.
- Learning curves, validation curves, overfitting, underfitting, optimization instability, gradient pathologies.
- Feature importance, attention/retrieval examples, nearest neighbors, pathway-level contributions, model uncertainty.
- Runtime, peak memory, I/O bottlenecks, repeated computation, avoidable serialization.

Use ablation to distinguish real signal from accidental complexity. Change one meaningful factor at a time when possible: feature group, prior, data source, post-processing rule, regularizer, architecture block, augmentation, calibration step, or ensemble member. If a component does not help robustly, remove or simplify it.

## Biological data and knowledge integration

Biomni should actively consider whether new data or prior knowledge can improve the method. Integration is especially valuable when the provided data are small, noisy, sparse, imbalanced, multimodal, or biologically structured.

Candidate resources include curated gene sets, pathways, ontologies, protein/domain annotations, sequences, structures, interaction networks, perturbation signatures, expression atlases, variant annotations, phenotype ontologies, drug/compound databases, cell-type markers, literature-derived relationships, and pretrained biomedical embeddings.

For every auxiliary resource, record provenance, license or usage restrictions when available, version/date, preprocessing, join keys, missing coverage, leakage risk, and whether it is train-safe. Prefer features and priors that would be available in realistic deployment.

## Derivation and model formulation

When a task calls for a new statistical, mechanistic, or optimization method, spend time deriving it. A derivation record should include:

- Variables, assumptions, objective, constraints, and notation.
- Biological interpretation of each term.
- Optimization or inference procedure.
- Expected computational complexity.
- Hyperparameters and how to estimate or eliminate them.
- Failure modes and diagnostics.
- Which theoretical properties must be preserved.

If an approximation is introduced for speed or stability, state what property is weakened and test the practical effect. Do not add mathematically decorative terms that lack an implementation path or evaluation plan.

## Foundation-model and large-model workflows

For foundation-model work, optimize the metric that predicts useful final performance under realistic compute. Often this is validation loss or downstream validation score after a short fixed budget, such as a five-minute training or adaptation proxy. Use small-scale experiments to rank objectives, data mixtures, tokenization/feature schemes, adapters, retrieval methods, and schedules before committing to expensive runs.

Track tokens/examples processed, wall time, memory, checkpoint size, validation loss, downstream metric, and stability. Prefer approaches that improve early learning curves and can scale without hidden data leakage. Use caching, mixed precision, gradient accumulation, adapters, frozen encoders, distilled features, and smaller proxy models when they preserve the relevant signal.

## Efficiency, simplification, and code quality

A method that is too slow, memory-heavy, fragile, or sprawling is usually not a good method. Profile after major changes. Common fixes include vectorization, sparse operations, caching immutable features, eliminating repeated I/O, reducing feature dimensionality, precomputing safe artifacts, batching, approximate nearest neighbors, simpler objectives, smaller models, and early stopping.

Limit manual hyperparameters. Prefer defaults estimated from data, validation-only selection, or theoretically grounded constants. Remove unused flags, dead branches, debug prints, heavyweight dependencies, and components that fail ablation.

## Anti-gaming and leakage audit

Before accepting any improvement, ask whether it could be caused by a benchmark artifact. Reject or quarantine methods that rely on:

- Hidden test labels, final leaderboard feedback, protected files, or evaluator internals.
- Sample order, file names, row IDs, path conventions, timestamps, or duplicate leakage.
- Target leakage through preprocessing, normalization, feature selection, imputation, augmentation, or auxiliary joins performed before splitting.
- Over-tuning to a single random split without repeated validation when variance is high.
- Post-processing that encodes benchmark-specific constants without scientific justification.
- Excessive complexity that only improves one measured number while harming guardrails.

A promoted method should pass sanity checks: shuffled-label degradation, no-signal baseline comparison, train-only preprocessing, subgroup review, repeated seeds when relevant, and protected hash verification.

**Prevent overfitting by optimizing on a smaller validation subset and testing on all of the data, including the validation set.** During iteration, evaluate candidates on the smaller validation subset for speed. Before accepting a candidate as an improvement — and always for the final method — re-run it on the full dataset (which includes the validation subset). A candidate that only wins on the subset but does not hold up on the full data is treated as overfitting to the subset and rejected.

## Final selection and delivery

**Entry precondition (hard gate).** Do not enter this phase until the termination gate passes: `python scripts/biomni_method_state.py stop-check --root <run_dir>` exits 0, i.e. `iteration >= max_iterations` **or** a hard blocker from the closed list has been recorded. Immediately before starting any deliverable (final report, package, method lock, or "wrap-up" summary), emit the one-line self-audit `STOP-GATE: iteration=<N>/<max>; hard_blocker=<none|infra|benchmark|user|data>; may_finalize=<yes|no>`. Building any final deliverable while `iteration < max_iterations` with `hard_blocker=none` is a protocol violation — return to Phase 4 instead. (Producing intermediate status snapshots and check-in reports during the run is fine and expected; this gate is specifically about *final* selection, packaging, and stopping.)

At the end, build a close set of finalists from different method families and simplification levels. Re-run them under the frozen benchmark. Prefer the simplest method whose improvement is robust and whose failure modes are understood. Run final ablations to trim nonessential components.

The final package must include:

```text
final_method/
  README.md
  method code
  reproduce.sh
  run_manifest.json
  benchmark_card.md
  method_card.md
  final_report.md
  dependency notes
```

The final report should include the problem, data, benchmark, baseline, final method, biological rationale, derivation if applicable, auxiliary resources, experiments, ablations, diagnostics, runtime/memory, leakage audit, limitations, and exact reproduction commands.

Do not hide failures. Instructive rejected ideas should remain in the experiment ledger so the user can understand why the final method was chosen.

## Reference files in this skill

- `references/method_development_loop.md`: detailed loop and decision rules.
- `references/benchmark_and_evaluation.md`: benchmark setup, metrics, protected files, repeated runs.
- `references/biological_reasoning_and_priors.md`: biological priors and representation ideas.
- `references/data_integration_and_search.md`: safe auxiliary data integration.
- `references/diagnostics_ablations_and_error_analysis.md`: diagnosis and ablation playbook.
- `references/derivation_and_model_formulation.md`: derivation records and theory-preserving changes.
- `references/foundation_model_optimization.md`: foundation-model proxy objectives and scaling-aware evaluation.
- `references/efficiency_simplification_and_scaling.md`: speed, memory, and simplification guidance.
- `references/integrity_and_reproducibility.md`: anti-gaming, leakage, and reproducibility audit.
- `references/state_and_memory.md`: durable state schema.
- `references/final_delivery_contract.md`: final artifact requirements.

Use the helper scripts and templates when useful, but do not let them constrain scientific creativity. They provide scaffolding; Biomni provides the method-development intelligence.

## Start now

Create or resume the durable run, freeze or scaffold the benchmark, gather actionable biological and computational evidence, build baselines and a diverse method portfolio, run the active development loop under the budget, select and trim finalists, audit for leakage and robustness, simplify and accelerate the locked method, then package the complete reproducible repository and report.

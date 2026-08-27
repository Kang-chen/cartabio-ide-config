# tusoskill

`tusoskill` is a Biomni skill for autonomous computational biology method development. It guides Biomni to act as the method designer, implementation engineer, evaluator, diagnostician, and final packager.

Use it when the task is to build, improve, or harden a biological or biomedical computational method: models, algorithms, feature pipelines, scoring functions, inference routines, foundation-model workflows, data-integration systems, or benchmarked analysis packages.

The skill emphasizes:

- Benchmark-first development with frozen evaluator contracts.
- Biology-aware hypothesis generation and data/knowledge integration.
- Adaptive, evidence-driven method search rather than one-shot coding.
- Durable feedback memory across iterations.
- Diagnostics, ablations, derivations, and simplification when they are useful.
- Strong protection against leakage, benchmark gaming, and unnecessary complexity.
- Overfitting guardrail: optimize on a smaller validation subset, then re-test on the full data (including the validation subset) before promotion.
- 50-iteration rounds with no wall-clock cap; the user prompt "keep iterating" extends the target by another 50 and continues the counter.
- Final delivery as a reproducible, documented, robust method package.

Typical installation:

```bash
unzip tusoskill_v1.zip -d /mnt/results/skills/
python /mnt/results/skills/tusoskill/scripts/verify_skill_package.py \
  --skill-root /mnt/results/skills/tusoskill
```

The skill root should be:

```text
/mnt/results/skills/tusoskill/SKILL.md
```

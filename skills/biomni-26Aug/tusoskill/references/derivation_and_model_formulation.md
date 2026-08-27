# Derivation and model formulation

Use derivations when a new score, model, loss, regularizer, constraint, or approximation could improve the method.

## Derivation record

Include:

- Problem statement.
- Variables and notation.
- Data-generating or biological assumptions.
- Objective, likelihood, risk, energy, or scoring rule.
- Constraints and invariances.
- Optimization or inference algorithm.
- Complexity and memory use.
- Hyperparameters and how to estimate them.
- Theoretical properties to preserve.
- Practical approximations and their tests.

## Principles

- Derive only what you can implement and evaluate.
- Prefer formulations that reduce hyperparameters or make them estimable.
- Keep units and biological interpretation clear.
- If the method relies on a theorem or invariant, stay close to the conditions needed for it.
- Test whether the derived term matters with ablation.

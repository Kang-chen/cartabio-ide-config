# Biological reasoning and priors

Good computational biology methods exploit real structure without hard-coding artifacts. Before and during method search, identify scientifically plausible priors.

## Common priors

- Pathway, gene set, and ontology grouping.
- Protein domains, motifs, structures, interactions, and sequence similarity.
- Cell type, tissue, disease, perturbation, dose, and time context.
- Batch effects, donor effects, covariates, censoring, and assay-specific noise.
- Network locality, modularity, sparsity, monotonicity, conservation, and mechanistic constraints.
- Known marker genes, variants, drug targets, phenotypes, and curated relationships.

## Representation ideas

- Aggregate sparse molecular features into pathway or module summaries.
- Use pretrained embeddings when they capture relevant biology and pass leakage checks.
- Build graph features from safe interaction, ontology, or similarity networks.
- Encode sequences or structures with compact descriptors when full models are too slow.
- Use multi-resolution features: raw, module-level, and global context.

## Guardrails

A biological prior is useful only if it helps validation or robustness. Avoid decorative biological terms that do not affect the implemented method. Test priors with ablations and subgroup analyses.

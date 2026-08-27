# Foundation-model optimization

For foundation-model or large-model method development, use small, honest proxies before expensive runs.

## Proxy objectives

A good proxy predicts final usefulness under a fixed compute budget. Examples:

- Validation loss after a short fixed training window.
- Downstream validation score after frozen-encoder feature extraction.
- Few-step adaptation score on a representative subset.
- Retrieval quality against training-safe evidence.
- Calibration or uncertainty quality for the deployment task.

Track wall time, memory, examples/tokens processed, checkpoint size, validation loss, downstream metric, and seed variance.

## Efficient adaptation options

- Frozen encoders with learned heads.
- Adapters or low-rank updates.
- Distilled features.
- Retrieval-augmented predictions.
- Mixed precision, gradient accumulation, and checkpointing.
- Smaller proxy models or reduced context during exploration.

## Integrity

Do not train or select on final evaluation labels. Keep data mixtures documented. Ensure pretrained resources are allowed and plausibly deployment-safe.

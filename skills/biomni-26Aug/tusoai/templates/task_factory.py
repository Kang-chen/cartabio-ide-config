"""Copy into the persistent task workspace and replace the TODO sections."""
from __future__ import annotations

import os
from typing import Any

from tusoai import Tusoai


def build_ai() -> Tusoai:
    """Construct a fresh provider client on every cluster node."""
    return Tusoai.from_api_key(
        api_key=os.environ["OPENAI_API_KEY"],
        provider="openai",
        model_settings={
            "pdf": {"model": "gpt-5.4-nano"},
            "construction": {"model": "gpt-5.4"},
            "optimization": {"model": "gpt-5.4-mini"},
        },
        max_tokens=15000,
    )


def build_task_bundle(ai: Tusoai) -> dict[str, Any]:
    """Build tasks exactly once, on the coordinator, then serialize them.

    Every user constraint must already be represented in the validated
    ``task_spec.json`` and propagated into task_description, target hints,
    global_hints, or evaluator assertions before this function returns.
    """
    raise NotImplementedError(
        "Implement build_task_bundle(): create MethodTask/DataTask objects and "
        "return the documented bundle fields."
    )

    # Required return shape:
    # return {
    #     "method_tasks": [method_task],
    #     "data_tasks": [],
    #     "reference_filename": "/mnt/shared-workspace/tusoai/<task>/runner.py",
    #     "task_description": "Concise scientific objective and metric.",
    #     "global_hints": ["Immutable constraints shared by every target."],
    #     "optimize_kwargs": {
    #         "timeout": 300,
    #         "bug_retries": 2,
    #         "prompt_samples": 5,
    #         "min_improvement": 0.01,
    #         "max_islands": None,
    #         "memory_limit_gb": 12,
    #     },
    #     "construction_cost": 0.0,
    # }

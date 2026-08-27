from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class EndpointSpec(BaseModel):
    """Scientific meaning of the single endpoint modeled in one run."""

    label_column: str
    task: Literal["regression", "classification"]
    unit: str | None = None
    unit_column: str | None = None
    scale: Literal["linear", "log10"] = "linear"
    qualifier_column: str | None = None
    class_mapping: dict[str, int] | None = None
    positive_class: str | None = None

    @model_validator(mode="after")
    def validate_endpoint(self) -> EndpointSpec:
        if self.task == "classification":
            if self.scale != "linear":
                raise ValueError("classification endpoints cannot use a log10 label scale")
            if self.class_mapping is not None and set(self.class_mapping.values()) != {0, 1}:
                raise ValueError("class_mapping must map exactly two labels to 0 and 1")
        elif self.class_mapping is not None:
            raise ValueError("class_mapping is only valid for classification")
        return self


class DatasetSpec(BaseModel):
    """Input table and the columns that define assay identity."""

    data_path: str
    smiles_column: str = "smiles"
    endpoint: EndpointSpec
    date_column: str | None = None
    assay_context_columns: list[str] = Field(default_factory=list)
    series_column: str | None = None
    compound_id_column: str | None = None
    allow_mixed_assays: bool = False


class RunConfig(BaseModel):
    """Choices that affect validation and model fitting, never assay semantics."""

    split: Literal["auto", "time", "scaffold", "cluster", "random", "deployment"] = "auto"
    deployment_path: str | None = None
    deployment_smiles_column: str = "smiles"
    test_fraction: float = Field(default=0.2, gt=0.05, lt=0.5)
    inner_splits: int = Field(default=3, ge=2, le=10)
    feature_sets: list[Literal["ecfp", "desc2d", "combined"]] = Field(
        default_factory=lambda: ["ecfp", "desc2d"]
    )
    models: list[str] | None = None
    confidence_level: float = Field(default=0.9, gt=0.5, lt=1.0)
    calibration_fraction: float = Field(default=0.2, gt=0.1, lt=0.4)
    probability_threshold: float = Field(default=0.5, gt=0.0, lt=1.0)
    n_bootstrap: int = Field(default=300, ge=0, le=5000)
    seed: int = 0

    @model_validator(mode="after")
    def validate_run(self) -> RunConfig:
        if self.split == "deployment" and not self.deployment_path:
            raise ValueError("deployment split requires deployment_path")
        if not self.feature_sets:
            raise ValueError("feature_sets cannot be empty")
        return self


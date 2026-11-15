"""Ground truth and the analyst view. Agent code (`fraudreview.agent`) must never import this
module (D-7, H5): a safety test enforces it."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from fraudreview.schemas import (
    SCHEMA_VERSION,
    AlertCase,
    AlertId,
    Driver,
    Model,
    Text,
    Timestamp,
)


class AlertTruth(Model):
    alert_id: AlertId
    label: bool
    shap_top5: Annotated[tuple[Driver, ...], Field(max_length=5)]

    @model_validator(mode="after")
    def _check(self) -> AlertTruth:
        features = [d.feature for d in self.shap_top5]
        if len(set(features)) != len(features):
            raise ValueError("each feature appears once among the drivers")
        return self


class Alert(Model):
    """The PRD's Alert, as a view for the analyst screen and evaluation (demo/replay.jsonl)."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    case: AlertCase
    truth: AlertTruth
    merchant: Text  # prefix stripped; shown to analysts, never sent to a model
    trans_ts: Timestamp

    @model_validator(mode="after")
    def _check(self) -> Alert:
        if self.case.alert_id != self.truth.alert_id:
            raise ValueError("case and truth describe different alerts")
        return self

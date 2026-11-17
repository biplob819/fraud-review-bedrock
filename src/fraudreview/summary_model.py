"""The summary `fraud evaluate` writes: results format v1 plus clearly named optional extensions
(E2.3, DSH-01).

`RunSummary` and `ExplanationSummary` in `schemas.py` stay exactly as they were (the v1 fixtures
and the frozen settings digest, which hashes `schemas.py`, depend on that). These two subclasses
add optional fields the dashboard needs: value-check shares, the faithfulness partial flag and
notes, mismatch detail, the alarms the run tripped, drift, the freeze digest and the audit-log
head. Unset extensions are left out of the
JSON, so a summary without them is byte-identical to a v1 one and reads back as a `RunSummary`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from fraudreview.schemas import (
    SHA256_RE,
    AlertId,
    Count,
    ExplanationSummary,
    Model,
    NonNeg,
    Rate,
    Real,
    RunSummary,
    Text,
    Unit,
    _freeze,
    _visible,
)

Note = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(_visible)]


def _omit_none(data: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    for name in names:
        if data.get(name) is None:
            data.pop(name, None)
    return data


class ValueShares(Model):
    """The value check's outcome mix over every reason checked (D-42)."""

    n: Count
    verified: Count
    contradicted: Count
    unverifiable: Count
    verified_share: Rate
    contradicted_share: Rate
    unverifiable_share: Rate

    @model_validator(mode="after")
    def _check(self) -> ValueShares:
        if self.verified + self.contradicted + self.unverifiable != self.n:
            raise ValueError("the three outcomes must add up to n")
        for share, k in (
            (self.verified_share, self.verified),
            (self.contradicted_share, self.contradicted),
            (self.unverifiable_share, self.unverifiable),
        ):
            if (share.k, share.n) != (k, self.n):
                raise ValueError("each share must be its count out of n")
        return self


class JudgeFaithfulness(Model):
    """One judge's coverage and support (D-43)."""

    claims_judged: Count
    claim_coverage: Unit  # claims judged out of claims extracted
    claims_supported: Rate | None
    explanations_fully_judged: Count
    explanations_fully_supported: Rate | None


class FaithfulnessDetail(Model):
    """What a faithfulness number needs beside it: `partial` and `notes` travel with the figure
    into the report and are never dropped (D-43)."""

    partial: bool
    notes: tuple[Note, ...]
    explanations: Count
    claims_extracted: Count
    judges: Annotated[dict[Text, JudgeFaithfulness], AfterValidator(_freeze)]

    @model_validator(mode="after")
    def _check(self) -> FaithfulnessDetail:
        if self.partial and not self.notes:
            raise ValueError("a partial faithfulness result must carry its notes")
        return self


class MismatchExample(Model):
    """Why an alert was picked as a mismatch example (EXP-06)."""

    alert_id: AlertId
    agreement: Unit | None  # None: picked for a protected mention, with no agreement score
    cited: tuple[Text, ...]
    positive_drivers: tuple[Text, ...]
    protected_mentions: Count


class EvaluatedExplanation(ExplanationSummary):
    driver_agreement_n: Count | None = None
    suspicious_no_clear: Count | None = None
    value_shares: ValueShares | None = None
    faithfulness_detail: FaithfulnessDetail | None = None
    mismatch_detail: tuple[MismatchExample, ...] | None = None

    @model_serializer(mode="wrap")
    def _serialise(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit_none(
            handler(self),
            (
                "driver_agreement_n",
                "suspicious_no_clear",
                "value_shares",
                "faithfulness_detail",
                "mismatch_detail",
            ),
        )

    @model_validator(mode="after")
    def _check_extensions(self) -> EvaluatedExplanation:
        detail = self.faithfulness_detail
        if detail is not None and set(detail.judges) != set(self.faithfulness):
            raise ValueError("faithfulness_detail must describe the same judges")
        if self.mismatch_detail is not None and tuple(
            m.alert_id for m in self.mismatch_detail
        ) != tuple(self.mismatch_examples):
            raise ValueError("mismatch_detail must follow mismatch_examples")
        return self


class AlarmRecord(Model):
    """An alarm band the run tripped (MON-02)."""

    metric: Text
    level: Literal["warn", "fail"]
    value: Real


class DriftRecord(Model):
    """The drift check of the run's cases against a reference sample (MON-03)."""

    feature: Text
    psi: NonNeg
    level: Literal["ok", "warn", "alarm"]


class AuditHead(Model):
    """The run's audit log head, so a summary names the log it came from."""

    seq: Count
    hash: Annotated[str, Field(pattern=SHA256_RE)]


class EvaluatedSummary(RunSummary):
    explanation: EvaluatedExplanation  # type: ignore[assignment]
    freeze_digest: Text | None = None
    audit_head: AuditHead | None = None
    minutes_per_alert: NonNeg | None = None  # the modelled analyst minutes behind the savings
    alarms: tuple[AlarmRecord, ...] | None = None  # () once checked and none tripped
    drift: tuple[DriftRecord, ...] | None = None

    @model_serializer(mode="wrap")
    def _serialise(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit_none(
            handler(self), ("freeze_digest", "audit_head", "minutes_per_alert", "alarms", "drift")
        )

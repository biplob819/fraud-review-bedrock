"""Typed records for the whole pipeline: results format v1 (Tech Spec §3).

Every model is frozen and strict: unknown fields are refused, sequences are tuples and mappings
are read-only (frozen is shallow, EvalKit L-8), floats reject NaN and infinity, booleans and
numbers are not coerced from strings, and datetimes must carry a time zone. Agent-visible data
(`AlertCase`) and ground truth (`fraudreview.truth`) are separate types, so the label cannot
reach a prompt (D-7).
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "1.0"

FeatureName = Literal[
    "amt_vs_usual",
    "hour",
    "category_freq",
    "merchant_first_seen",
    "card_history_n",
    "amount",
    "category",
    "risk_score",
]
Provider = Literal["bedrock", "anthropic"]  # D-27
Effort = Literal["low", "medium", "high", "xhigh", "max"]
CLAUSE_RE = re.compile(r"P[0-9]{1,3}\.[0-9]{1,3}")  # fullmatch: no trailing newline
SHA256_RE = r"^[0-9a-f]{64}$"
ALERT_ID = r"^a-[0-9a-f]{16}$"  # opaque: 16 hex characters
DIGIT_RUN = re.compile(r"[0-9]{12,}")  # a run this long could be a card or account number
# the 14 Sparkov categories (Fraud Dataset Benchmark, sparknov)
Category = Literal[
    "entertainment",
    "food_dining",
    "gas_transport",
    "grocery_net",
    "grocery_pos",
    "health_fitness",
    "home",
    "kids_pets",
    "misc_net",
    "misc_pos",
    "personal_care",
    "shopping_net",
    "shopping_pos",
    "travel",
]
ThresholdMetric = Literal[
    "leakage",
    "recall_end_to_end",
    "auto_clear_rate",
    "auto_clear_precision",
    "cost_per_1000_usd",
    "latency_p95_stage2_ms",
    "schema_valid",
    "clauses_valid",
    "fields_valid",
    "protected_mentions",
]

Unit = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
NonNeg = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
Real = Annotated[float, Field(allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]
Stage = Annotated[int, Field(ge=1, le=2)]  # strict int: True or 1.0 are not a stage


FILLERS = frozenset("\u115f\u1160\u3164\uffa0")  # letters by category, blank on screen


def _visible(text: str) -> str:
    """An allow-list rule: no control or format characters, and at least one letter or digit
    that is not a filler. Symbols and punctuation alone do not count (some render as nothing)."""
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text):
        raise ValueError("control or format characters are not allowed")
    if not any(unicodedata.category(ch)[0] in "LN" and ch not in FILLERS for ch in text):
        raise ValueError("text must contain a visible letter or digit")
    return text


Text = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_visible)]
Comment = Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(_visible)]
StageKey = Literal["stage1", "stage2"]


def _opaque_id(value: str) -> str:
    if DIGIT_RUN.search(value):
        raise ValueError("an id may not contain a run of 12 or more digits")
    return value


AlertId = Annotated[str, Field(pattern=ALERT_ID), AfterValidator(_opaque_id)]


def _utc(value: datetime) -> datetime:
    try:
        return value.astimezone(UTC)  # one instant, one serialisation: the audit hash is stable
    except OverflowError as exc:
        raise ValueError("timestamp is out of range") from exc


Timestamp = Annotated[AwareDatetime, AfterValidator(_utc)]  # a zone is required


class FrozenDict(dict):  # type: ignore[type-arg]
    """A read-only, hashable dict for mapping fields of frozen models. It copies and pickles as
    a new FrozenDict; `dict.__setitem__(m, ...)` on the base class is the one bypass left."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if getattr(self, "_sealed", False):
            raise TypeError("this mapping is read-only")
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("this mapping is read-only")

    def _read_only(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("this mapping is read-only")

    def __reduce__(self) -> tuple[Any, ...]:
        return (FrozenDict, (dict(self),))

    def __copy__(self) -> FrozenDict:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenDict:
        return FrozenDict(copy.deepcopy(dict(self), memo))

    __setitem__ = __delitem__ = clear = pop = popitem = _read_only
    setdefault = update = _read_only  # type: ignore[assignment]
    __ior__ = _read_only

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(tuple(sorted(self.items())))


def _freeze[T](d: dict[Any, T]) -> dict[Any, T]:
    return FrozenDict(d)


def _unique(values: tuple[Any, ...]) -> tuple[Any, ...]:
    if len(set(values)) != len(values):
        raise ValueError("values must be unique")
    return values


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False, strict=True)


# ---------- SCH-01 alerts ----------


class Features(Model):
    amt_vs_usual: NonNeg | None
    hour: Annotated[int, Field(ge=0, le=23)]
    category_freq: Unit
    merchant_first_seen: bool
    card_history_n: Count


class AlertCase(Model):
    """Everything a model may see, and nothing else (D-7)."""

    alert_id: AlertId
    ts_hour: Annotated[int, Field(ge=0, le=23)]
    ts_dow: Annotated[int, Field(ge=0, le=6)]
    category: Category
    amount: NonNeg
    features: Features
    risk_score: Unit

    @model_validator(mode="after")
    def _check(self) -> AlertCase:
        if self.ts_hour != self.features.hour:
            raise ValueError("ts_hour and features.hour must agree")
        return self


class Driver(Model):
    feature: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    contribution: Real  # SHAP, log-odds; > 0 pushes toward fraud


# ---------- SCH-02 decisions ----------


def _check_reason(reason: str) -> str:
    return _visible(reason)  # the same allow-list rule as every other text field


Reason = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(_check_reason)]


def _check_clause(clause_id: str) -> str:
    if not CLAUSE_RE.fullmatch(clause_id):
        raise ValueError(f"malformed clause id {clause_id!r}")
    return clause_id


ClauseId = Annotated[
    str, Field(pattern=r"^P[0-9]{1,3}\.[0-9]{1,3}$", max_length=8), AfterValidator(_check_clause)
]  # the pattern also reaches the JSON schema used for structured output


class _StageOutput(Model):
    reasons: Annotated[tuple[Reason, ...], Field(min_length=1, max_length=5)]
    policy_clauses: Annotated[
        tuple[ClauseId, ...], Field(min_length=1, max_length=10), AfterValidator(_unique)
    ]
    fields_used: Annotated[tuple[FeatureName, ...], Field(min_length=1), AfterValidator(_unique)]
    confidence: Unit


class Stage1Output(_StageOutput):
    action: Literal["clear", "pass"]


class Stage2Output(_StageOutput):
    action: Literal["clear", "escalate", "hold"]


class Tokens(Model):
    input: Count
    output: Count  # includes thinking
    cache_read: Count = 0
    cache_write: Count = 0


class TriageDecision(Model):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    decision_id: Text
    alert_id: AlertId
    stage: Stage
    provider: Provider
    model: Text
    effort: Effort
    prompt_hash: Text
    policy_version: Text
    input_hash: Text
    output: Stage1Output | Stage2Output | None
    error: Literal["schema", "refusal", "transport"] | None
    attempts: Annotated[int, Field(ge=1, le=10)]
    tokens: Tokens
    cost_usd: NonNeg
    latency_ms: Count
    request_id: Text | None
    cache_hit: bool
    at: Timestamp

    @field_validator("output", mode="before")
    @classmethod
    def _output_by_stage(cls, value: Any, info: ValidationInfo) -> Any:
        """Choose the output class from `stage`, so a stage-2 clear reads back as stage 2."""
        stage = info.data.get("stage")
        if isinstance(value, dict) and stage in (1, 2):
            model = Stage1Output if stage == 1 else Stage2Output
            if info.mode == "json":  # stay strict: re-read as JSON, where arrays become tuples
                return model.model_validate_json(json.dumps(value))
            return model.model_validate(value)
        return value

    @model_validator(mode="after")
    def _check(self) -> TriageDecision:
        if (self.output is None) == (self.error is None):
            raise ValueError("a decision has either an output or an error, never both or neither")
        if self.output is not None:
            want = Stage1Output if self.stage == 1 else Stage2Output  # stage is 1 or 2
            if type(self.output) is not want:
                raise ValueError(f"stage {self.stage} output must be {want.__name__}")
        return self


# ---------- SCH-03 audit ----------


class Disposition(Model):
    kind: Literal["disposition"] = "disposition"
    alert_id: AlertId
    final: Literal["auto_cleared", "hold", "escalate"]
    decided_at_stage: Stage
    router_refusals: tuple[Text, ...] = ()
    decision_ids: Annotated[tuple[Text, ...], Field(min_length=1), AfterValidator(_unique)]
    queue: Literal["priority", "standard"] | None

    @model_validator(mode="after")
    def _check(self) -> Disposition:
        want = {"auto_cleared": None, "hold": "priority", "escalate": "standard"}[self.final]
        if self.queue != want:
            raise ValueError(f"{self.final} must have queue {want!r}")
        if self.final == "hold" and self.decided_at_stage == 1:
            raise ValueError("stage 1 cannot hold an alert (it clears or passes on)")
        return self


class AnalystAction(Model):
    kind: Literal["analyst_action"] = "analyst_action"
    alert_id: AlertId
    action: Literal["confirm_fraud", "clear", "override"]
    comment: Comment | None
    analyst: Text

    @model_validator(mode="after")
    def _check(self) -> AnalystAction:
        if self.action == "override" and not (self.comment and self.comment.strip()):
            raise ValueError("an override needs a comment")
        return self


class DecisionEvent(Model):
    kind: Literal["decision"] = "decision"
    decision: TriageDecision


class AuditEvent(Model):
    """One line of the append-only audit log (D-12). `kind` is carried by the payload, which is
    a discriminated union. `hash` is the sha256 of the canonical JSON of the whole event without
    its `hash` field (so seq, at, alert_id, prev_hash and payload are all covered), computed and
    verified in A2.2. Build events through the constructor: `model_copy(update=...)` skips
    validation."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    seq: Count
    at: Timestamp
    alert_id: AlertId
    payload: Annotated[DecisionEvent | Disposition | AnalystAction, Field(discriminator="kind")]
    prev_hash: Annotated[str, Field(pattern=r"^$|^[0-9a-f]{64}$")]
    hash: Annotated[str, Field(pattern=SHA256_RE)]

    @property
    def kind(self) -> str:
        return self.payload.kind

    @model_validator(mode="after")
    def _check(self) -> AuditEvent:
        inner = (
            self.payload.decision.alert_id
            if isinstance(self.payload, DecisionEvent)
            else self.payload.alert_id
        )
        if inner != self.alert_id:
            raise ValueError("payload alert_id does not match the event")
        if (self.seq == 0) != (self.prev_hash == ""):
            raise ValueError("only the first event (seq 0) has an empty prev_hash")
        if self.prev_hash == self.hash:
            raise ValueError("an event's hash cannot equal its prev_hash")
        return self


# ---------- SCH-04 summary ----------


class Rate(Model):
    k: Count
    n: Count
    point: Unit
    lo: Unit
    hi: Unit

    @model_validator(mode="after")
    def _check(self) -> Rate:
        if self.k > self.n:
            raise ValueError("k > n")
        expect = self.k / self.n if self.n else 0.0
        if abs(self.point - expect) > 1e-12:
            raise ValueError(f"point must be k/n = {expect}")
        if not (self.lo <= self.point <= self.hi):
            raise ValueError("need lo <= point <= hi")
        return self


class Interval(Model):
    point: Real
    lo: Real
    hi: Real

    @model_validator(mode="after")
    def _check(self) -> Interval:
        if not (self.lo <= self.point <= self.hi):
            raise ValueError("need lo <= point <= hi")
        return self


def _non_negative(iv: Interval) -> Interval:
    if iv.lo < 0:
        raise ValueError("this interval cannot go below 0")
    return iv


def _within_unit(iv: Interval) -> Interval:
    if iv.lo < 0 or iv.hi > 1:
        raise ValueError("this interval must lie within [0, 1]")
    return iv


NonNegInterval = Annotated[Interval, AfterValidator(_non_negative)]
UnitInterval = Annotated[Interval, AfterValidator(_within_unit)]


class BaselineSummary(Model):
    threshold: Unit
    recall_val: Unit
    precision_val: Unit
    recall_test: Rate
    precision_test: Rate
    alert_rate_test: Unit


class ExplanationSummary(Model):
    schema_valid: Rate
    clauses_valid: Rate
    fields_valid: Rate
    values_true: Rate
    protected_mentions: Count
    driver_agreement: UnitInterval
    faithfulness: Annotated[dict[Text, UnitInterval], AfterValidator(_freeze)]
    judge_mode: Literal["dual", "single", "none"]
    mismatch_examples: Annotated[tuple[AlertId, ...], AfterValidator(_unique)]

    @model_validator(mode="after")
    def _check(self) -> ExplanationSummary:
        want = {"none": 0, "single": 1, "dual": 2}[self.judge_mode]
        if len(self.faithfulness) != want:
            raise ValueError(f"judge_mode {self.judge_mode} needs {want} faithfulness entries")
        return self


class ThresholdResult(Model):
    metric: ThresholdMetric
    op: Literal[">=", "<=", "=="]
    value: Real
    passed: bool | None = None  # None until evaluated


class LatencySummary(Model):
    p50: Count
    p95: Count

    @model_validator(mode="after")
    def _check(self) -> LatencySummary:
        if self.p50 > self.p95:
            raise ValueError("p50 cannot exceed p95")
        return self


ErrorKind = Literal["schema", "refusal", "transport"]


class RunSummary(Model):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: Text
    split: Literal["dev", "eval", "golden"]
    data_label: Literal["simulated"] = "simulated"
    n_alerts: Count
    n_frauds: Count
    policy_version: Text
    prompt_hashes: Annotated[dict[StageKey, Text], AfterValidator(_freeze)]
    models: Annotated[dict[StageKey, Text], AfterValidator(_freeze)]
    provider: Provider
    baseline: BaselineSummary
    leakage: Rate
    recall_end_to_end: Rate
    auto_clear_rate: Rate
    auto_clear_precision: Rate
    stage1_clear_rate: Rate
    stage2_reach_rate: Rate
    queue_mix: Annotated[dict[Literal["hold", "escalate"], Count], AfterValidator(_freeze)]
    minutes_saved_per_1000: NonNegInterval
    cost_per_1000_usd: NonNegInterval
    latency_ms: Annotated[dict[StageKey, LatencySummary], AfterValidator(_freeze)]
    explanation: ExplanationSummary
    errors: Annotated[dict[ErrorKind, Count], AfterValidator(_freeze)]
    thresholds: Annotated[tuple[ThresholdResult, ...], Field(min_length=1)]
    cost_total_usd: NonNeg
    started_at: Timestamp
    finished_at: Timestamp | None

    @model_validator(mode="after")
    def _check(self) -> RunSummary:
        if self.n_frauds > self.n_alerts:
            raise ValueError("n_frauds > n_alerts")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at before started_at")
        # every fraud is either auto-cleared (leaked) or reaches a person (caught)
        for name in ("leakage", "recall_end_to_end"):
            if getattr(self, name).n != self.n_frauds:
                raise ValueError(f"{name}.n must equal n_frauds")
        if self.leakage.k + self.recall_end_to_end.k != self.n_frauds:
            raise ValueError("leaked plus caught frauds must equal n_frauds")
        for name in ("auto_clear_rate", "stage1_clear_rate", "stage2_reach_rate"):
            if getattr(self, name).n != self.n_alerts:
                raise ValueError(f"{name}.n must equal n_alerts")
        if self.auto_clear_precision.n != self.auto_clear_rate.k:
            raise ValueError("auto_clear_precision.n must equal the number of auto-clears")
        if sum(self.queue_mix.values()) + self.auto_clear_rate.k != self.n_alerts:
            raise ValueError("auto-clears plus queued alerts must equal n_alerts")
        # cleared alerts are legitimate (precision hits) or leaked frauds
        if self.auto_clear_precision.k != self.auto_clear_rate.k - self.leakage.k:
            raise ValueError("auto-clear precision must equal auto-clears minus leaked frauds")
        if self.stage1_clear_rate.k > self.auto_clear_rate.k:
            raise ValueError("stage-1 clears cannot exceed auto-clears")
        if self.recall_end_to_end.k > self.n_alerts - self.auto_clear_rate.k:
            raise ValueError("caught frauds cannot exceed the alerts that reached a person")
        if sum(self.errors.values()) > 2 * self.n_alerts:
            raise ValueError("more errors than stage calls")
        if self.stage1_clear_rate.k + self.stage2_reach_rate.k > self.n_alerts:
            raise ValueError("an alert is cleared at stage 1 or reaches stage 2, not both")
        metrics = [t.metric for t in self.thresholds]
        if len(set(metrics)) != len(metrics):
            raise ValueError("each threshold metric may appear once")
        leakage = [t for t in self.thresholds if t.metric == "leakage"]
        if len(leakage) != 1 or leakage[0].op != "==" or leakage[0].value != 0:
            raise ValueError("thresholds must hold exactly one leakage row: leakage == 0 (D-10)")
        if leakage[0].passed is not None and leakage[0].passed != (self.leakage.k == 0):
            raise ValueError("the leakage threshold result must match the leakage count")
        if self.finished_at is not None and any(t.passed is None for t in self.thresholds):
            raise ValueError("a finished run must have every threshold evaluated")
        return self

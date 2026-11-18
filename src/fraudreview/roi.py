"""ROI model formulas (B3.1). The workbook docs/roi/roi-model.xlsx holds the same formulas.

Defaults for r, f and lambda come from docs/measurements/summary-eval2.json (simulated data);
tests/test_roi.py checks that they still match. The other defaults are illustrative.
"""

from __future__ import annotations

from dataclasses import dataclass

NO_PAYBACK = "no payback"

# From summary-eval2.json (simulated data).
AUTO_CLEAR_RATE = 0.088  # auto_clear_rate.point: 44 of 500 alerts
FRAUD_SHARE = 0.11  # 55 frauds in 500 alerts
LEAKAGE_UPPER_BOUND = 0.0530110549512538  # leakage.hi: exact 95% upper bound for 0 of 55
MINUTES_PER_ALERT = 4.0  # minutes_per_alert in the summary (a timing assumption)


@dataclass(frozen=True)
class RoiInputs:
    alerts_per_month: float = 20000  # A, illustrative
    minutes_per_alert: float = MINUTES_PER_ALERT  # m
    cost_per_hour: float = 40.0  # c, illustrative, USD
    auto_clear_rate: float = AUTO_CLEAR_RATE  # r
    capacity_share: float = 0.5  # k, illustrative
    fixed_cost: float = 60000.0  # B, illustrative one-off cost, USD
    fraud_share: float = FRAUD_SHARE  # f
    leakage_rate: float = LEAKAGE_UPPER_BOUND  # lambda, an upper bound, not an estimate
    avg_fraud_loss: float = 500.0  # v, illustrative, USD


@dataclass(frozen=True)
class RoiOutputs:
    hours_saved: float
    gross_saving: float
    leakage_loss: float
    net_saving: float
    payback_months: float | str  # NO_PAYBACK when net_saving <= 0


def compute(i: RoiInputs) -> RoiOutputs:
    hours = i.alerts_per_month * i.auto_clear_rate * i.minutes_per_alert / 60
    gross = hours * i.cost_per_hour * i.capacity_share
    leak = i.leakage_rate * i.fraud_share * i.alerts_per_month * i.avg_fraud_loss
    net = gross - leak
    payback: float | str = NO_PAYBACK if net <= 0 else i.fixed_cost / net
    return RoiOutputs(hours, gross, leak, net, payback)


def break_even_leakage(i: RoiInputs) -> float:
    """The leakage rate at which net saving is zero (above it, no payback)."""
    gross = compute(i).gross_saving
    return gross / (i.fraud_share * i.alerts_per_month * i.avg_fraud_loss)

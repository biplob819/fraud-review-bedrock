"""Point-in-time card features (Tech Spec §2.2, FR2, H6).

Every feature except `hour` is computed from the same card's earlier transactions only.
"Earlier" is the order `(ts, split, row)`: train, then validation, then test, each sorted by time
as N1.2 stores it, ties kept in stored order. History carries across splits, which is legitimate
because validation and test come later in time; it is what a production system would know.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from fraudreview.data import SPLITS

FEATURE_COLUMNS = (
    "amt_vs_usual",
    "hour",
    "category_freq",
    "merchant_first_seen",
    "card_history_n",
)
MIN_HISTORY_FOR_USUAL = 3  # amt_vs_usual is null with fewer prior transactions


def ordered_history(splits: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """All transactions in the documented order, with `split` and `order` columns added."""
    parts = []
    for rank, name in enumerate(SPLITS):
        part = splits[name].copy()
        part["split"] = name
        part["_rank"] = rank
        part["_row"] = np.arange(len(part))
        parts.append(part)
    history = pd.concat(parts, ignore_index=True)
    history = history.sort_values(["ts", "_rank", "_row"], kind="mergesort").reset_index(drop=True)
    history["order"] = np.arange(len(history))
    return history.drop(columns=["_rank", "_row"])


def _prior_median(amounts: pd.Series) -> pd.Series:
    prior = amounts.shift(1).expanding(min_periods=MIN_HISTORY_FOR_USUAL).median()
    return pd.Series(prior, index=amounts.index)


def add_features(history: pd.DataFrame) -> pd.DataFrame:
    """Add the feature columns to an ordered history (see `ordered_history`). Rows must already
    be in history order; each value uses only rows earlier in that order for the same card."""
    if not history["order"].is_monotonic_increasing:
        raise ValueError("history must be in order")
    out = history.copy()
    by_card = out.groupby("card_id", sort=False)
    out["card_history_n"] = by_card.cumcount().astype("int64")
    prior_in_category = out.groupby(["card_id", "category"], sort=False).cumcount()
    out["category_freq"] = np.where(
        out["card_history_n"] > 0,
        prior_in_category / out["card_history_n"].where(out["card_history_n"] > 0, 1),
        0.0,
    )
    out["merchant_first_seen"] = out.groupby(["card_id", "merchant"], sort=False).cumcount() == 0
    usual = by_card["amt"].transform(_prior_median)
    usual = pd.Series(usual, index=out.index)
    usual = usual.where(usual > 0)  # a zero median (impossible in Sparkov) gives null, not inf
    out["amt_vs_usual"] = out["amt"] / usual  # NaN where under 3 prior transactions
    out["hour"] = out["ts"].dt.hour.astype("int64")
    return out


def build_features(splits: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Ordered history of all splits with the five features (BAS-01)."""
    return add_features(ordered_history(splits))

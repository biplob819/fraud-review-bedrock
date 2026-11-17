"""Analyst review screen (D4.1, Tech Spec §7). Replay only (D-15): every case comes from the
committed `demo/replay.jsonl`, nothing calls a model or a cloud service, and analyst actions go
to a per-session hash-chained audit log the visitor can download.

Run with `streamlit run demo/app.py`. Importing this module does nothing; `main()` draws the page.
All data is simulated.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import streamlit as st

try:
    from fraudreview import demo
except ImportError:  # running from a checkout without an installed package
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from fraudreview import demo

SIM = "simulated"
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ACTION_LABEL = {
    "hold": "Hold (priority queue)",
    "escalate": "Escalate (standard queue)",
    "pass": "Pass to stage 2",
    "clear": "Clear",
    None: "No output (error)",
}
REVIEW_LABEL = {"confirm_fraud": "Confirm fraud", "clear": "Clear", "override": "Override"}


def num(value: float, fmt: str = "{:.3f}") -> str:
    """Every number on the screen carries the simulated label (NFR5)."""
    return f"{fmt.format(value)} ({SIM})"


def case_label(record: dict[str, Any]) -> str:
    case = record["alert"]["case"]
    q = "P" if record["disposition"]["queue"] == "priority" else "S"
    return f"[{q}] {case['alert_id']}  risk {case['risk_score']:.4f} ({SIM})"


@st.cache_data
def load_queue() -> list[dict[str, Any]]:
    return demo.queue()


@st.cache_data
def clause_text(clause_id: str) -> str:
    return demo.policy_text(clause_id)


@st.cache_data
def clause_title(clause_id: str) -> str:
    return demo.policy_title(clause_id) or "not in the current policy"


def session_log() -> Any:
    if "audit_log" not in st.session_state:
        st.session_state["audit_log"] = demo.session_log()
    return st.session_state["audit_log"]


def transaction_column(record: dict[str, Any]) -> None:
    alert = record["alert"]
    case, f = alert["case"], alert["case"]["features"]
    st.subheader("Transaction")
    st.metric(f"Baseline risk score ({SIM})", f"{case['risk_score']:.4f}")
    st.markdown(f"**Merchant:** {alert['merchant']}")
    st.markdown(f"**Amount:** {num(case['amount'], '${:,.2f}')}")
    st.markdown(f"**Category:** `{case['category']}`")
    st.markdown(f"**Time:** {alert['trans_ts']} ({DAYS[case['ts_dow']]}, hour {case['ts_hour']})")
    usual = f["amt_vs_usual"]
    rows = [
        ("Amount vs card's usual", "missing" if usual is None else num(usual, "{:.2f}x")),
        ("Category share on this card", num(f["category_freq"], "{:.1%}")),
        ("First purchase at merchant", "yes" if f["merchant_first_seen"] else "no"),
        ("Earlier purchases on card", num(f["card_history_n"], "{:,}")),
    ]
    st.table({"Feature": [r[0] for r in rows], "Value": [r[1] for r in rows]})


def decision_block(view: dict[str, Any], expanded_clauses: bool) -> None:
    conf = "n/a" if view["confidence"] is None else num(view["confidence"], "{:.2f}")
    st.markdown(
        f"**Stage {view['stage']}** ({view['model']}): **{ACTION_LABEL[view['action']]}**, "
        f"confidence {conf}"
    )
    if view["error"]:
        st.warning(f"The model's reply failed the {view['error']} check; the router held it.")
    st.markdown("**Reasons**")
    st.markdown("\n".join(f"{i}. {r}" for i, r in enumerate(view["reasons"], 1)) or "none")
    st.markdown("**Policy clauses** (expand for the rule)")
    for cid in view["policy_clauses"]:
        with st.expander(f"{cid}: {clause_title(cid)}", expanded=expanded_clauses):
            st.write(clause_text(cid))
    if view["fields_used"]:
        st.markdown("**Fields used:** " + ", ".join(f"`{x}`" for x in view["fields_used"]))


def agent_column(record: dict[str, Any]) -> None:
    disp = record["disposition"]
    st.subheader("Agent decision")
    final = "Held: priority queue" if disp["queue"] == "priority" else "Escalated: standard queue"
    st.info(f"{final} (decided at stage {disp['decided_at_stage']})")
    if disp["router_refusals"]:
        st.markdown("**Router refusals:** " + ", ".join(f"`{r}`" for r in disp["router_refusals"]))
    decisions = record["decisions"]
    final_view = decisions[-1]
    decision_block(final_view, expanded_clauses=False)
    for earlier in decisions[:-1]:
        with st.expander(f"Earlier: stage {earlier['stage']} decision"):
            decision_block(earlier, expanded_clauses=False)


def shap_column(record: dict[str, Any]) -> None:
    st.subheader("Score drivers")
    st.caption(f"SHAP contributions to the baseline's risk score, log-odds ({SIM})")
    drivers = record["alert"]["truth"]["shap_top5"]
    top = max((abs(d["contribution"]) for d in drivers), default=1.0) or 1.0
    rows = []
    for d in drivers:
        c = d["contribution"]
        width = max(2, round(100 * abs(c) / top))
        color = "#c0392b" if c > 0 else "#2e86c1"
        # feature names match ^[a-z][a-z0-9_]*$ (schema) and c is a float: nothing to escape
        rows.append(
            f'<div style="margin:6px 0"><div style="font-size:0.85em">'
            f"<code>{d['feature']}</code> {c:+.2f} ({SIM})</div>"
            f'<div style="background:{color};height:10px;width:{width}%;border-radius:3px">'
            "</div></div>"
        )
    st.markdown("".join(rows), unsafe_allow_html=True)
    st.caption("Red pushes toward fraud, blue away from it.")


def review_buttons(record: dict[str, Any], log: Any) -> None:
    alert_id = record["alert"]["case"]["alert_id"]
    st.subheader("Your call")
    comment = st.text_input(
        "Comment (required to override)", key=f"comment-{alert_id}", max_chars=2000
    )
    c1, c2, c3 = st.columns(3)
    clicked = None
    if c1.button("Confirm fraud", key=f"confirm-{alert_id}", type="primary"):
        clicked = "confirm_fraud"
    if c2.button("Clear", key=f"clear-{alert_id}"):
        clicked = "clear"
    if c3.button("Override with comment", key=f"override-{alert_id}"):
        clicked = "override"
    if clicked is None:
        return
    if clicked == "override" and not comment.strip():
        st.error("An override needs a comment.")
        return
    try:
        event = demo.review(alert_id, clicked, comment, log)
    except ValueError as exc:
        st.error(f"Not recorded: {exc}")
        return
    st.success(f"Recorded {REVIEW_LABEL[clicked]} as audit event {event.seq}.")


def audit_trail(log: Any) -> None:
    st.subheader("Session audit trail")
    st.caption(
        "Hash-chained and verified on every read. It lives only in this browser session; "
        "download it to keep it."
    )
    events = demo.session_events(log)
    if not events:
        st.write("No actions yet in this session.")
        return
    st.dataframe(
        {
            "seq": [e.seq for e in events],
            "at (UTC)": [e.at.strftime("%Y-%m-%d %H:%M:%S") for e in events],
            "alert": [e.alert_id for e in events],
            "action": [REVIEW_LABEL.get(getattr(e.payload, "action", ""), "") for e in events],
            "comment": [getattr(e.payload, "comment", None) or "" for e in events],
            "prev_hash": [e.prev_hash[:12] for e in events],
            "hash": [e.hash[:12] for e in events],
        },
        hide_index=True,
        width="stretch",
    )
    st.success(f"Chain verified: {len(events)} events, head {events[-1].hash[:12]}")
    st.download_button(
        "Download audit log (JSONL)",
        data=demo.session_bytes(log),
        file_name="session-audit.jsonl",
        mime="application/jsonl",
    )


def main() -> None:
    st.set_page_config(page_title="Fraud review: analyst screen", layout="wide")
    st.title("Analyst review screen")
    st.caption(
        "Replay of a recorded evaluation run on simulated card data. No model is called and no "
        "real card is involved. Every number on this page is simulated."
    )
    cases = load_queue()
    log = session_log()
    n_hold = sum(r["disposition"]["queue"] == "priority" for r in cases)
    with st.sidebar:
        st.header("Queue")
        st.caption(
            f"{n_hold} held (priority) and {len(cases) - n_hold} escalated (standard) cases "
            f"({SIM}). Priority first, then by risk."
        )
        index = st.selectbox("Case", range(len(cases)), format_func=lambda i: case_label(cases[i]))
    record = cases[index or 0]
    left, middle, right = st.columns([1, 1.5, 0.9], gap="large")
    with left:
        transaction_column(record)
    with middle:
        agent_column(record)
    with right:
        shap_column(record)
    st.divider()
    review_buttons(record, log)
    with st.expander("Simulated ground truth (never shown to the agent)"):
        label = "fraud" if record["alert"]["truth"]["label"] else "not fraud"
        st.write(f"The simulated dataset labels this alert {label}.")
    st.divider()
    audit_trail(log)


if __name__ == "__main__":  # `streamlit run` executes the script as __main__
    main()

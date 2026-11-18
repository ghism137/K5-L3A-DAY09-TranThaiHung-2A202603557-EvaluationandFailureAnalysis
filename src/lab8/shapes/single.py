"""Shape B — one ReAct agent.

`lab8.runtime.run_react` with max_steps=8. `decide` picks one read tool per step
(lookup_order -> compute_compensation) and then returns a final. It never calls
`issue_refund`: after the loop the shape asks the host, which is the only writer.

Each decide() step stands for one model turn, so it is counted as one MockLLM call
(that is what makes B cost more than the fixed workflow). The decision itself is
deterministic and reads only structured tool results, never the ticket text, so a
ticket that overstates the delay or cites a superseded clause cannot change the quote.

Per-order cap: the full quote goes to `durability.issue_refund`; the host subtracts
what other claims already received for the order.

Trace: claim_id, one event per ReAct step (tool name + allowlisted result), the host
outcome. Not written: ticket, claim row, order row, headers.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace

MAX_STEPS = 8


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id("single", claim_id, seed))

    def done(status: str, amount: int, reason: str | None) -> RunResult:
        trace.add("decision", {"status": status, "proposal_amount": int(amount), "reason": reason})
        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=amount,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    budget.add_tool(tool_cost())
    claim = get_claim_view(conn, claim_id)
    if not claim["found"]:
        return done("failed", 0, "unknown_claim")
    trace.add("claim", {"claim_id": claim_id, "has_order_id": bool(claim["order_id"])})

    # The agent's context holds the ticket (it is data, only used as model input size).
    ticket = claim["ticket"]
    principal_id = claim["principal_id"]
    order_id = claim["order_id"]
    state: dict[str, Any] = {}

    def decide(seen: dict[str, Any]) -> dict[str, Any]:
        last = seen.get("last_tool")
        # One model turn per step, counted in the budget.
        complete(budget, seed=seed, purpose="react", prompt=ticket if last is None else str(last))
        result = seen.get("last_result")
        if last is not None:
            trace.add("step", {"tool": last, "result": _trace_view(last, result)})
        if last is None:
            if not order_id:
                return {"final": {"status": "needs_info", "reason": "missing_order_id"}}
            return {"tool": "lookup_order", "args": {"order_id": order_id, "principal_id": principal_id}}
        if last == "lookup_order":
            if not result["visible"]:
                return {"final": {"status": "escalated", "reason": result["reason"]}}
            state["order"] = result["order"]
            return {"tool": "compute_compensation", "args": {"order": result["order"]}}
        if last == "compute_compensation":
            amount = int(result["amount_vnd"])
            if amount <= 0:
                return {"final": {"status": "denied", "reason": result["reason"], "amount": 0}}
            return {"final": {"status": "quote", "amount": amount}}
        return {"final": {"status": "failed", "reason": "unexpected_tool"}}

    loop = run_react(
        conn,
        budget,
        observation={"claim": {"claim_id": claim_id}},
        decide=decide,
        max_steps=MAX_STEPS,
    )
    final = loop["final"]
    status = final["status"]

    if status in ("needs_info", "escalated", "denied", "failed"):
        complete(budget, seed=seed, purpose="draft", prompt=status)
        return done(status, 0, final.get("reason") or final.get("error_reason"))

    # status == "quote": the host is the only writer. Full quote, crash_at forwarded.
    amount = int(final["amount"])
    try:
        outcome = issue_refund(conn, claim_id, amount, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise
    trace.add("host", {"ok": bool(outcome["ok"]), "reason": outcome["reason"]})
    complete(budget, seed=seed, purpose="draft", prompt=f"proposal={amount}")
    if not outcome["ok"]:
        return done("failed" if outcome["conflict"] else "denied", amount, outcome["reason"])
    return done("resolved", amount, None)


def _trace_view(tool: str, result: Any) -> Any:
    """Trace only allowlisted tool output (lookup_order and compute_compensation already are)."""
    if tool == "lookup_order":
        return {"visible": result["visible"], "reason": result["reason"], "order": result.get("order")}
    if tool == "compute_compensation":
        return {k: result[k] for k in ("amount_vnd", "reason", "clause_ids")}
    return {"note": "not traced"}

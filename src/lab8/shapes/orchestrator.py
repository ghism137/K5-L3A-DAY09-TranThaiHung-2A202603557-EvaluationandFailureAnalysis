"""Shape C — orchestrator plus read-only investigators.

1. The orchestrator reads the claim and builds a handoff with `make_handoff`
   (allowlisted fields, read-only tools). No parent_secret, headers or claim row in it.
2. `run_readonly_investigators` runs lookup_order and compute_compensation.
   Investigators have no write registry (worker=True).
3. The orchestrator takes the quote from the investigator's order view and is the only
   caller of `durability.issue_refund`. It passes the FULL quote and forwards crash_at.
4. Trace: the sanitized handoff and the investigator's allowlisted result. Never the
   parent context. An LLM summary is not used as a sanitizer, the allowlist is.

Model calls (MockLLM): one for the plan, one for the investigator, one for the draft.
They carry no money decision.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace


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
    trace = Trace(make_run_id("orchestrator", claim_id, seed))

    def done(status: str, amount: int, reason: str | None) -> RunResult:
        trace.add("decision", {"status": status, "proposal_amount": int(amount), "reason": reason})
        return finish(
            conn,
            shape="orchestrator",
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

    complete(budget, seed=seed, purpose="plan", prompt=claim["ticket"])
    if not claim["order_id"]:
        complete(budget, seed=seed, purpose="draft", prompt="needs_info")
        return done("needs_info", 0, "missing_order_id")

    # Handoff: allowlist only. facts stay empty, the investigator reads structured data itself.
    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="quote_order",
        facts={},
        allowed_tools=["lookup_order", "compute_compensation"],
    )
    trace.add("handoff", safe_dump(handoff))

    complete(budget, seed=seed, purpose="investigate", prompt="quote_order")
    investigated = run_readonly_investigators(conn, budget, handoff, principal_id=claim["principal_id"])
    order_view = investigated["tools"].get("lookup_order", {})
    if not order_view.get("visible"):
        trace.add("investigator", {"lookup_order": {"visible": False, "reason": order_view.get("reason")}})
        complete(budget, seed=seed, purpose="draft", prompt="escalated")
        return done("escalated", 0, order_view.get("reason"))

    quote = investigated["tools"]["compute_compensation"]
    trace.add("investigator", {"lookup_order": {"visible": True, "order": order_view["order"]}, "quote": quote})
    amount = int(quote["amount_vnd"])

    if amount <= 0:
        complete(budget, seed=seed, purpose="draft", prompt="denied")
        return done("denied", 0, quote["reason"])

    # Only the orchestrator writes, through the host gate.
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

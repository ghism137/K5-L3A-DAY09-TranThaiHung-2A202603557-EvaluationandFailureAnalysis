"""Shape A — deterministic workflow.

Signature (do not change):

    run_claim(claim_id, *, seed, store_path, crash_at=None) -> RunResult

Fixed Python steps. Money and approval stay in code:

1. Read the claim through `get_claim_view` (not SELECT *).
2. Missing order_id -> `needs_info`. The ledger is not touched.
3. `lookup_order`. Not visible -> `escalated`. No phone, address, item or note is copied.
4. Quote from the order row with `compute_compensation`. Delay numbers in the ticket are ignored.
5. Quote 0 -> `denied`, `issue_refund` is not called.
6. Quote > 0 -> `durability.issue_refund(conn, claim_id, <FULL quote>, crash_at=crash_at)`.
   The host checks the claim's own approval and subtracts earlier refunds on the same order,
   so the shape never subtracts anything itself. `InjectedCrash` is not caught.
7. MockLLM only drafts the reply text (one call). It never sees or chooses an amount.

Trace: only fields from the read-tool allowlists are written (claim_id, order view, quote).
The ticket text, the claim row and the order row are never written.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import compute_compensation, get_claim_view, lookup_order, tool_cost
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
    trace = Trace(make_run_id("workflow", claim_id, seed))

    def done(status: str, amount: int, reason: str | None) -> RunResult:
        trace.add("decision", {"status": status, "proposal_amount": int(amount), "reason": reason})
        return finish(
            conn,
            shape="workflow",
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=amount,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    # Step 1: read the claim.
    budget.add_tool(tool_cost())
    claim = get_claim_view(conn, claim_id)
    if not claim["found"]:
        return done("failed", 0, "unknown_claim")
    trace.add("claim", {"claim_id": claim_id, "has_order_id": bool(claim["order_id"])})

    # Step 2: no order id -> ask the customer. Never read an order id out of the ticket.
    if not claim["order_id"]:
        complete(budget, seed=seed, purpose="draft", prompt="needs_info")
        return done("needs_info", 0, "missing_order_id")

    # Step 3: order must belong to the principal.
    budget.add_tool(tool_cost())
    order_view = lookup_order(conn, claim["order_id"], claim["principal_id"])
    if not order_view["visible"]:
        trace.add("order", {"visible": False, "reason": order_view["reason"]})
        complete(budget, seed=seed, purpose="draft", prompt="escalated")
        return done("escalated", 0, order_view["reason"])
    order = order_view["order"]
    trace.add("order", {"visible": True, "order": order})

    # Step 4: the amount comes from code, from the order row.
    budget.add_tool(tool_cost())
    quote = compute_compensation(conn, order)
    trace.add("quote", quote)
    amount = int(quote["amount_vnd"])

    # Step 5: nothing to pay.
    if amount <= 0:
        complete(budget, seed=seed, purpose="draft", prompt="denied")
        return done("denied", 0, quote["reason"])

    # Step 6: ask the host. Full quote, crash_at forwarded, InjectedCrash propagates.
    try:
        outcome = issue_refund(conn, claim_id, amount, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise
    trace.add("host", {"ok": bool(outcome["ok"]), "reason": outcome["reason"]})

    # Step 7: model drafts the reply only.
    complete(budget, seed=seed, purpose="draft", prompt=f"proposal={amount}")
    if not outcome["ok"]:
        status = "failed" if outcome["conflict"] else "denied"
        return done(status, amount, outcome["reason"])
    return done("resolved", amount, None)

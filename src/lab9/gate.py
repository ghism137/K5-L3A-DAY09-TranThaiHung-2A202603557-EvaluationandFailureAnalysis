"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    problems += _report_problems(pr, "PR")
    problems += _report_problems(base, "baseline")
    if problems and not isinstance(pr.get("rows"), list):
        return problems                      # nothing below can run on a report without rows

    pr_rows, base_rows = pr.get("rows") or [], base.get("rows") or []
    # (item, seed) set: dropped, duplicated or cherry-picked trials all change it.
    pr_keys = [(r.get("item_id"), r.get("seed")) for r in pr_rows]
    base_keys = [(r.get("item_id"), r.get("seed")) for r in base_rows]
    if len(set(pr_keys)) != len(pr_keys):
        problems.append(f"PR has {len(pr_keys) - len(set(pr_keys))} duplicated (item, seed) row(s)")
    if set(pr_keys) != set(base_keys):
        gone, extra = sorted(set(base_keys) - set(pr_keys), key=str), sorted(set(pr_keys) - set(base_keys), key=str)
        problems.append(f"(item, seed) set differs from the baseline's: {len(gone)} missing {gone[:3]}, "
                        f"{len(extra)} extra {extra[:3]}")

    # a graded spec metric whose value is missing/None silently drops out of the mean
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in _graded_ids()]
    for mid in graded:
        bad = [r.get("item_id") for r in pr_rows if mid not in r or r[mid] is None]
        if bad:
            problems.append(f"{len(bad)} row(s) have no value for graded metric {mid} (first: {bad[0]})")

    # reported metrics must be what the rows say: a hand-edited report no longer matches its own rows
    problems += _recompute_problems(pr, graded, spec)

    # slice tags: relabelling an item out of a slice hides its regression from the slice gate
    pr_tags, base_tags = _slice_tags(pr_rows), _slice_tags(base_rows)
    for item_id, tags in sorted(pr_tags.items(), key=str):
        if len(tags) > 1:
            problems.append(f"{item_id}: slice tags differ between its own rows {sorted(map(sorted, tags))}")
        elif item_id in base_tags and tags != base_tags[item_id]:
            problems.append(f"{item_id}: slice tags {sorted(next(iter(tags)))} differ from the baseline's "
                            f"{sorted(next(iter(base_tags[item_id])))}")

    if base_spec is not None:
        problems += _ratchet_problems(spec, base_spec, pr, base)
    return problems


def _graded_ids() -> set[str]:
    from lab9.graders import GRADERS            # local import: gate must stay importable without graders
    return set(GRADERS)


def _is_bad_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isfinite(v)


def _report_problems(rep: dict, who: str) -> list[str]:
    out = []
    for key in ("manifest", "rows", "metrics"):
        if key not in rep:
            out.append(f"{who} report has no {key!r}")
    if out:
        return out
    out += [f"{who} metric {k} is {v}" for k, v in rep["metrics"].items() if _is_bad_number(v)]
    out += [f"{who} row {r.get('item_id')}@{r.get('seed')} has non-finite {k}={v}"
            for r in rep["rows"] for k, v in r.items() if _is_bad_number(v)]
    m = rep["manifest"]
    want = m.get("items", 0) * len(m.get("seeds", []))
    if len(rep["rows"]) != want:
        out.append(f"{who} has {len(rep['rows'])} rows, manifest says {m.get('items')} items x "
                   f"{len(m.get('seeds', []))} seeds = {want}")
    if rep.get("evaluator_failures"):
        out.append(f"{who} has {len(rep['evaluator_failures'])} evaluator failure(s), "
                   f"first: {rep['evaluator_failures'][0]}")
    return out


SAFETY_IDS = ("secret_leak", "approval_bypass")      # aggregated as a count, everything else as a mean


def _recompute_problems(pr: dict, graded: list[str], spec: dict) -> list[str]:
    out = []
    rows = pr["rows"]
    for mid in graded:
        vals = [r[mid] for r in rows if r.get(mid) is not None]
        if mid not in pr["metrics"] or pr["metrics"][mid] is None or not vals:
            continue
        want = sum(vals) if mid in SAFETY_IDS else sum(vals) / len(vals)
        if abs(float(pr["metrics"][mid]) - want) > 5e-4:
            out.append(f"metric {mid} reports {pr['metrics'][mid]} but its rows give {want:.4f} (hand-edited?)")
    by_item: dict = {}
    for r in rows:
        by_item.setdefault(r.get("item_id"), []).append(bool(r.get("pass_rate")))
    if by_item and pr["metrics"].get("pass_5") is not None:
        want = sum(all(v) for v in by_item.values()) / len(by_item)
        if abs(float(pr["metrics"]["pass_5"]) - want) > 5e-4:
            out.append(f"metric pass_5 reports {pr['metrics']['pass_5']} but its rows give {want:.4f} (hand-edited?)")
    for name, fn in (("pass_at", stats.pass_at_k), ("pass_hat", stats.pass_hat_k)):
        for key, val in pr["metrics"].items():
            if not key.startswith(name + "_") or val is None or not by_item:
                continue
            k = int(key.rsplit("_", 1)[1])
            if min(len(v) for v in by_item.values()) < k:
                continue
            want = sum(fn(len(v), sum(v), k) for v in by_item.values()) / len(by_item)
            if abs(float(val) - want) > 5e-4:
                out.append(f"metric {key} reports {val} but its rows give {want:.4f} (hand-edited?)")
    return out


def _slice_tags(rows: list[dict]) -> dict:
    tags: dict = {}
    for r in rows:
        tags.setdefault(r.get("item_id"), set()).add(frozenset(r.get("slices") or []))
    return tags


def _looser(key: str, new, old) -> bool:
    """True when threshold `key` went from `old` to `new` in the lenient direction."""
    if key in ("max", "max_increase"):
        return new is None or new > old
    if key == "min":
        return new is None or new < old
    if key in ("no_significant_drop",):
        return bool(old) and not bool(new)
    return False


def _threshold_loosened(new: dict, old: dict, what: str) -> list[str]:
    out = [f"{what}: threshold '{k}' went {old[k]} -> {new.get(k)} (ratchet: only stricter)"
           for k in old if _looser(k, new.get(k), old[k])]
    if old.get("blocking") and not new.get("blocking"):
        out.append(f"{what}: blocking metric became non-blocking")
    return out


def _ratchet_problems(spec: dict, base_spec: dict, pr: dict, base: dict) -> list[str]:
    out = []
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        if pr["manifest"].get("eval_set_sha") == base["manifest"].get("eval_set_sha"):
            out.append(f"eval_set_version bumped {base_spec.get('eval_set_version')!r} -> "
                       f"{spec.get('eval_set_version')!r} but the eval set is unchanged: not a new baseline")
        return out                                  # a genuinely new eval-set version restarts the ratchet
    new_metrics = {m["id"]: m for m in spec.get("metrics", [])}
    for old in base_spec.get("metrics", []):
        new = new_metrics.get(old["id"])
        if new is None:
            out.append(f"metric {old['id']} was removed from the spec")
            continue
        out += _threshold_loosened({**new.get("threshold", {}), "blocking": new.get("blocking")},
                                   {**old.get("threshold", {}), "blocking": old.get("blocking")},
                                   f"metric {old['id']}")
    new_slices = {s["name"]: s for s in spec.get("slices", [])}
    for old in base_spec.get("slices", []):
        new = new_slices.get(old["name"])
        if new is None:
            out.append(f"slice {old['name']} was removed from the spec")
            continue
        if new.get("metric") != old.get("metric"):
            out.append(f"slice {old['name']}: metric changed {old.get('metric')} -> {new.get('metric')}")
        out += _threshold_loosened({k: new.get(k) for k in ("min", "no_significant_drop", "blocking")},
                                   {k: old[k] for k in ("min", "no_significant_drop", "blocking") if k in old},
                                   f"slice {old['name']}")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    bs = spec.get("bootstrap", {})
    blocked, lines = [], []
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note = "ok", ""
        if not a:
            # an empty slice cannot be verified, and "no rows" must never read as "no regression"
            verdict, value = "fail", None
            note = "no PR rows carry this slice"
        else:
            value = sum(a.values()) / len(a)
            if "min" in sl and value < sl["min"]:
                verdict = "fail"
            if sl.get("no_significant_drop"):
                keys = sorted(set(a) & set(b))
                if keys:
                    d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                       n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        label = "ok" if verdict == "ok" else ("BLOCK" if sl.get("blocking") else "warn")
        shown = "n/a" if value is None else f"{value:.4f}"
        lines.append(f"  {sid:<34} {shown:<8} {json.dumps({k: sl[k] for k in ('min',) if k in sl}):<14} "
                     f"n={len(a):<3} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

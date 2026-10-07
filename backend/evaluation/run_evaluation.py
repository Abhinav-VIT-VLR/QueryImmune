"""Run the versioned policy-only shadow corpus; no database or model is contacted."""
from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from queryimmune_db.ai.intent import QueryIntent
from queryimmune_db.application.nl2sql import NL2SQLAnalysis, ValidatedIntentPolicy
from queryimmune_db.api.models import ExecutionRequest
from queryimmune_db.behavior import BehaviorAnalyzer, BehaviorAwarePipeline
from queryimmune_db.domain.identity import Actor
from queryimmune_db.security.sql_validation import IntentSqlValidator
from queryimmune_db.services.policy import Decision

EVALUATION = Path(__file__).resolve().parent
RESULTS = EVALUATION / "results"
MODES = ("A_plain_nl2sql", "B_static_rules", "C_anomaly_detection", "D_queryimmune")
NOW = datetime.fromisoformat("2026-10-07T12:00:00+00:00")


class FixtureBehaviorRepository:
    def __init__(self, profile, history):
        self.profile, self.history = profile, history

    def load(self, actor_id):
        return self.profile, self.history


def _case_context(case):
    intent = QueryIntent.model_validate(case["intent"])
    request = ExecutionRequest(action=intent.action, limit=intent.limit)
    actor = Actor("00000000-0000-0000-0000-000000000001", "evaluation-actor", "Synthetic evaluator", frozenset(case["actor_roles"]))
    return intent, request, actor


def _evaluate(case, mode):
    intent, request, actor = _case_context(case)
    start = time.perf_counter_ns()
    drift_detected = False
    if mode == "A_plain_nl2sql":
        decision = Decision("allow", (), 0, "Unprotected generation baseline; shadow only")
        validation = None
    else:
        validation = IntentSqlValidator().validate(case["request"], intent, case["sql"])
        analysis = NL2SQLAnalysis(intent, validation, "fixed-evaluation-candidate-v1", "fixture", case["sql"])
        base = ValidatedIntentPolicy(analysis, 100, 25).decide(request, actor)
        if mode == "B_static_rules":
            decision = base
        elif mode == "C_anomaly_detection":
            decision = base
            if base.state == "allow":
                features = dict(base.feature_snapshot)
                assessment = BehaviorAnalyzer().analyze(features, case.get("profile"), case.get("history", []), NOW)
                drift_detected = assessment.drift
                # Explicit benchmark configuration for the anomaly-only comparator.
                state = "block" if assessment.deviation >= 60 else "review" if assessment.deviation >= 35 else "allow"
                decision = Decision(state, assessment.reasons, assessment.deviation,
                                    "Anomaly baseline threshold", "eval-anomaly-threshold-v1", feature_snapshot=base.feature_snapshot)
        else:
            repository = FixtureBehaviorRepository(case.get("profile"), [
                {"created_at": datetime.fromisoformat(item["created_at"]), "feature_snapshot": item["feature_snapshot"]}
                for item in case.get("history", [])
            ])
            pipeline = BehaviorAwarePipeline(ValidatedIntentPolicy(analysis, 100, 25), repository)
            decision = pipeline.decide(request, actor)
            snap = decision.feature_snapshot.get("behavior", {})
            drift_detected = bool(snap.get("drift"))
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    codes = list(decision.reason_codes)
    if mode == "D_queryimmune" and decision.feature_snapshot.get("risk_reasons"):
        codes = list(decision.feature_snapshot["risk_reasons"])
    return {
        "case_id": case["id"], "label": case["label"], "dangerous": case["dangerous"],
        "mode": mode, "decision": decision.state.upper(), "reason_codes": ";".join(codes),
        "intent_sql_valid": None if validation is None else validation.valid,
        "mismatch_detected": bool(validation is not None and case["label"] == "intent_mismatch" and not validation.valid),
        "drift_expected": bool(case.get("drift_expected", case["label"] == "unusual_legitimate")),
        "drift_detected": drift_detected, "executor_invoked": False,
        "policy_shadow_latency_ms": round(elapsed_ms, 4),
    }


def _ratio(numerator, denominator):
    return None if denominator == 0 else round(numerator / denominator, 6)


def _write_plot(rows):
    # Dependency-free SVG bars from measured policy-shadow p50 timings.
    p50 = {mode: statistics.median(r["policy_shadow_latency_ms"] for r in rows if r["mode"] == mode) for mode in MODES}
    max_value = max(p50.values(), default=1) or 1
    width, bar_h, gap, left, top = 760, 30, 24, 230, 48
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="250" viewBox="0 0 {width} 250">',
             '<rect width="100%" height="100%" fill="#0b1220"/>',
             '<text x="24" y="28" fill="#e5eefb" font-family="sans-serif" font-size="16">Policy shadow median latency (ms)</text>']
    for i, mode in enumerate(MODES):
        y = top + i * (bar_h + gap)
        bar = max(2, (width - left - 35) * p50[mode] / max_value)
        lines.extend([f'<text x="20" y="{y+20}" fill="#cbd5e1" font-family="sans-serif" font-size="12">{mode}</text>',
                      f'<rect x="{left}" y="{y}" width="{bar:.2f}" height="{bar_h}" rx="5" fill="#45d6b5"/>',
                      f'<text x="{left+bar+8:.2f}" y="{y+20}" fill="#e5eefb" font-family="sans-serif" font-size="12">{p50[mode]:.4f}</text>'])
    lines.append('</svg>')
    (RESULTS / "policy_shadow_latency.svg").write_text("\n".join(lines), encoding="utf-8")


def main():
    manifest = json.loads((EVALUATION / "cases.v1.json").read_text(encoding="utf-8"))
    rows = [_evaluate(case, mode) for case in manifest["cases"] for mode in MODES]
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / "case_results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    metrics = {"dataset_version": manifest["dataset_version"], "run_scope": "local deterministic policy shadow; no model, API, PostgreSQL, or executor invocation",
               "cases": len(manifest["cases"]), "cases_per_mode": len(manifest["cases"]), "results_status": "MEASURED_ON_DEVELOPMENT_CORPUS_ONLY",
               "executor_invoked": False, "held_out_evaluation": "NOT_MEASURED",
               "full_api_model_postgres_latency_ms_p50": None, "full_api_model_postgres_latency_ms_p95": None,
               "live_executor_prevention_count": None, "live_executor_prevention_status": "NOT_MEASURED; shadow runner deliberately has no executor",
               "modes": {}}
    for mode in MODES:
        subset = [row for row in rows if row["mode"] == mode]
        dangerous = [row for row in subset if row["dangerous"]]
        safe = [row for row in subset if not row["dangerous"]]
        mismatches = [row for row in subset if row["label"] == "intent_mismatch"]
        drift_cases = [row for row in subset if row["drift_expected"]]
        timings = sorted(row["policy_shadow_latency_ms"] for row in subset)
        metrics["modes"][mode] = {
            "dangerous_query_detection_rate": _ratio(sum(row["decision"] != "ALLOW" for row in dangerous), len(dangerous)),
            "false_positive_rate_block_only": _ratio(sum(row["decision"] == "BLOCK" for row in safe), len(safe)),
            "safe_review_rate": _ratio(sum(row["decision"] == "REVIEW" for row in safe), len(safe)),
            "intent_sql_mismatch_detection_rate": _ratio(sum(row["mismatch_detected"] for row in mismatches), len(mismatches)),
            "workload_drift_detection_rate": _ratio(sum(row["drift_detected"] for row in drift_cases), len(drift_cases)),
            "dangerous_candidates_held_before_execution_in_shadow": sum(row["dangerous"] and row["decision"] != "ALLOW" and not row["executor_invoked"] for row in subset),
            "shadow_policy_latency_ms_p50": round(statistics.median(timings), 4),
            "shadow_policy_latency_ms_p95": round(timings[min(len(timings)-1, int(.95 * len(timings)))], 4),
            "added_end_to_end_latency_ms": None,
        }
    plain_p50 = metrics["modes"]["A_plain_nl2sql"]["shadow_policy_latency_ms_p50"]
    for value in metrics["modes"].values():
        value["shadow_policy_latency_ms_delta_vs_plain"] = round(value["shadow_policy_latency_ms_p50"] - plain_p50, 4)
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    _write_plot(rows)
    # Decision comparison plot is a fixed-corpus visualization, not a statistical claim.
    labels = {"ALLOW":"#45d6b5", "REVIEW":"#f5bd5b", "BLOCK":"#ff6685"}
    height = 190 + len(MODES) * 34
    bars = ['<svg xmlns="http://www.w3.org/2000/svg" width="760" height="%d">' % height,
            '<rect width="100%" height="100%" fill="#0b1220"/>',
            '<text x="24" y="28" fill="#e5eefb" font-family="sans-serif" font-size="16">Decision counts by mode (development corpus)</text>']
    for i, mode in enumerate(MODES):
        y = 55 + i * 34
        counts = {state: sum(row["mode"] == mode and row["decision"] == state for row in rows) for state in labels}
        bars.append(f'<text x="20" y="{y+16}" fill="#cbd5e1" font-family="sans-serif" font-size="11">{mode}</text>')
        x = 240
        for state, color in labels.items():
            w = counts[state] * 24
            bars.append(f'<rect x="{x}" y="{y}" width="{w}" height="22" fill="{color}"/><text x="{x+4}" y="{y+15}" fill="#0b1220" font-size="11">{state} {counts[state]}</text>')
            x += w + 6
    bars.append('</svg>')
    (RESULTS / "decision_comparison.svg").write_text("\n".join(bars), encoding="utf-8")
    print(json.dumps({"cases": len(manifest["cases"]), "modes": len(MODES), "results": str(RESULTS), "status": metrics["results_status"]}))


if __name__ == "__main__":
    main()

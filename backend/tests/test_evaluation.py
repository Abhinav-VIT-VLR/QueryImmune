import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_versioned_evaluation_corpus_covers_required_classes():
    dataset = json.loads((ROOT / "evaluation" / "cases.v1.json").read_text(encoding="utf-8"))
    labels = {case["label"] for case in dataset["cases"]}
    assert {"normal", "unusual_legitimate", "sensitive", "destructive", "intent_mismatch", "compromised_user", "mass_extraction"} <= labels


def test_generated_metrics_mark_unmeasured_full_stack_values_explicitly():
    result_dir = ROOT / "evaluation" / "results"
    metrics = json.loads((result_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["results_status"] == "MEASURED_ON_DEVELOPMENT_CORPUS_ONLY"
    assert metrics["full_api_model_postgres_latency_ms_p50"] is None
    assert metrics["live_executor_prevention_count"] is None
    assert metrics["executor_invoked"] is False
    assert all((result_dir / name).is_file() for name in ("case_results.csv", "decision_comparison.svg", "policy_shadow_latency.svg"))

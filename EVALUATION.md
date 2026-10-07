# Evaluation

Run the deterministic shadow evaluation from `backend/`:

```powershell
python evaluation/run_evaluation.py
```

The versioned development manifest is `backend/evaluation/cases.v1.json`. It includes normal, unusual legitimate/drift, sensitive, destructive, intent mismatch, compromised-role, and mass-extraction candidates. The runner compares:

A. Plain NL2SQL candidate pass-through.
B. Production SQL AST validator plus deterministic role/scope policy.
C. B plus an explicitly configured anomaly threshold.
D. Production behavior-aware pipeline and explainable risk engine.

Outputs are regenerated in `backend/evaluation/results/`: `case_results.csv`, `metrics.json`, `decision_comparison.svg`, and `policy_shadow_latency.svg`. Metrics derive from the manifest and code; they are not manually entered. The JSON labels them `MEASURED_ON_DEVELOPMENT_CORPUS_ONLY`, gives per-mode denominators through raw case rows, and leaves full API/model/PostgreSQL latency and live executor-boundary prevention as null/unmeasured.

## Limits

This corpus has seven cases (five labeled dangerous and two safe), is a fixed development corpus, and is not held out from policy design. It is too small for general accuracy or confidence claims. The runner is shadow-only: it invokes no model, network, database, or executor. Its latency is local policy processing only and excludes generation and persistence. Drift detection is assessed on a single labeled history fixture. Run the database-backed API tests separately before making executor-boundary claims. Never present these values as production or clinical performance.

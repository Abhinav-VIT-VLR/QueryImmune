# QueryImmune evaluation

Run `py evaluation/run_evaluation.py` from `backend/`. The versioned JSON manifest is the fixed development corpus. The runner feeds its static candidates through the repository validator, policy, behavior analyzer, and risk engine. It never invokes the executor, PostgreSQL, or a model provider.

Outputs in `evaluation/results/` are generated artifacts: per-case CSV, aggregate JSON, and SVG decision/latency plots. Re-running the same code and manifest regenerates them. Decision latency measures only local shadow-policy processing; it excludes model, network, API, database, and execution time. End-to-end latency and live executor prevention remain explicitly unmeasured. The corpus is small, synthetic, and used for development, not held-out accuracy claims.

Modes are deliberately scoped: A passes the candidate without checks; B applies the production AST validator and deterministic role/scope policy; C adds a versioned deviation threshold to B; D uses the production behavior aware pipeline and explainable risk engine. The C threshold is a comparator configuration, not a claim about a separately deployed detector.

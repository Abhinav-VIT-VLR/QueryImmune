"""Behavioral workload analysis and explainable risk policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import psycopg
from queryimmune_db.api.models import ExecutionRequest
from queryimmune_db.domain.identity import Actor
from queryimmune_db.services.policy import Decision, RequestPipeline


@dataclass(frozen=True)
class BehaviorAssessment:
    deviation: float
    similarity: float | None
    drift: bool
    drift_kind: str
    sample_count: int
    reasons: tuple[str, ...]


def _set(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {str(v).lower() for v in value}
    return set()


class BehaviorAnalyzer:
    """Compares bounded action metadata; never sees SQL text or patient rows."""

    def analyze(self, features: Mapping[str, Any], profile: Mapping[str, Any] | None,
                history: Sequence[Mapping[str, Any]], now: datetime | None = None) -> BehaviorAssessment:
        now = now or datetime.now(timezone.utc)
        summary = dict((profile or {}).get("feature_summary") or {})
        baseline_n = int((profile or {}).get("approved_sample_count") or 0)
        current_action = str(features.get("action") or features.get("intent_action") or "unknown")
        current_tables = _set(features.get("tables") or features.get("intent_tables"))
        current_columns = _set(features.get("columns") or features.get("intent_columns"))
        signature = str(features.get("semantic_signature") or current_action)
        reasons: list[str] = []
        # Drift logic below also consults the learned signatures during warm-up.
        # Profiles with a sample count but no summary must remain a valid state.
        signatures = _set(summary.get("signatures"))
        if baseline_n < 10 or not summary:
            reasons.append("BASELINE_WARMUP")
            deviation, similarity = 0.0, None
        else:
            actions = _set(summary.get("actions"))
            tables = _set(summary.get("tables"))
            # Jaccard feature similarity is intentionally interpretable and bounded.
            parts = [1.0 if current_action in actions else 0.0]
            if tables:
                parts.append(len(tables & current_tables) / max(1, len(tables | current_tables)))
            if signatures:
                parts.append(1.0 if signature.lower() in signatures else 0.0)
            similarity = sum(parts) / len(parts)
            deviation = round(100.0 * (1.0 - similarity), 2)
            if current_action not in actions:
                reasons.append("UNSEEN_ACTION_PATTERN")
            if tables and current_tables and not (tables & current_tables):
                reasons.append("UNSEEN_TABLE_PATTERN")
            expected_roles = _set(summary.get("roles"))
            current_roles = _set(features.get("role_context"))
            if expected_roles and current_roles and not (expected_roles & current_roles):
                deviation = min(100.0, deviation + 15)
                reasons.append("ROLE_CONTEXT_DEVIATION")
            rows = features.get("result_size")
            cap = summary.get("result_size_max")
            if isinstance(rows, int) and isinstance(cap, (int, float)) and rows > max(10, cap * 2):
                deviation = min(100.0, deviation + 20)
                reasons.append("RESULT_SIZE_DEVIATION")
            hour = str(now.hour)
            active_hours = _set(summary.get("active_hours"))
            if active_hours and hour not in active_hours:
                deviation = min(100.0, deviation + 10)
                reasons.append("UNUSUAL_TIME_PATTERN")
            hourly = sum(1 for e in history if _time(e.get("created_at")) and _time(e.get("created_at")) >= now - timedelta(hours=1))
            if hourly >= 20:
                deviation = min(100.0, deviation + 20)
                reasons.append("UNUSUAL_REQUEST_FREQUENCY")

        recent = [e for e in history if _time(e.get("created_at")) and _time(e.get("created_at")) >= now - timedelta(days=7)]
        prior = [e for e in history if _time(e.get("created_at")) and now - timedelta(days=30) <= _time(e.get("created_at")) < now - timedelta(days=7)]
        drift, kind = False, "none"
        if baseline_n >= 10 and signatures and signature.lower() not in signatures and len(recent) >= 5:
            recent_signatures = [str((e.get("feature_snapshot") or {}).get("semantic_signature", "")).lower() for e in recent]
            if recent_signatures.count(signature.lower()) / max(1, len(recent_signatures)) >= 0.6:
                drift, kind = True, "gradual"
                reasons.append("LEGITIMATE_WORKLOAD_DRIFT")
        # Require adequately sampled windows; compare action distributions to distinguish sustained drift.
        if len(recent) >= 5 and len(prior) >= 10:
            recent_actions = [str((e.get("feature_snapshot") or {}).get("action", "")) for e in recent]
            prior_actions = [str((e.get("feature_snapshot") or {}).get("action", "")) for e in prior]
            r_share = recent_actions.count(current_action) / len(recent_actions)
            p_share = prior_actions.count(current_action) / len(prior_actions)
            if r_share >= 0.6 and p_share < 0.2:
                drift, kind = True, "gradual" if len(recent) >= 10 else "abrupt"
                reasons.append("LEGITIMATE_WORKLOAD_DRIFT" if kind == "gradual" else "WORKLOAD_DRIFT")
        return BehaviorAssessment(deviation, similarity, drift, kind, baseline_n, tuple(reasons))


def _time(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


@dataclass(frozen=True)
class RiskAssessment:
    score: float
    level: str
    state: str
    reasons: tuple[str, ...]
    factors: Mapping[str, float]


class ExplainableRiskEngine:
    """Additive risk factors; callers must run hard policy before this assessment."""

    def assess(self, features: Mapping[str, Any], behavior: BehaviorAssessment) -> RiskAssessment:
        factors: dict[str, float] = {}
        codes: list[str] = list(behavior.reasons)
        mismatches = features.get("mismatch_codes") or []
        if features.get("validated") is False or mismatches:
            factors["intent_sql_mismatch"] = 100.0
            codes.append("INTENT_SQL_MISMATCH")
        else:
            factors["intent_sql_mismatch"] = 0.0
        operation = str(features.get("operation") or features.get("intent_operation") or "read").lower()
        factors["operation_risk"] = 65.0 if operation in {"delete", "update", "insert", "write"} else 0.0
        if factors["operation_risk"]:
            codes.append("DESTRUCTIVE_OR_MUTATING_OPERATION")
        sensitivity = str(features.get("sensitivity") or "standard").lower()
        factors["data_sensitivity"] = {"standard": 0.0, "sensitive": 15.0, "restricted": 60.0}.get(sensitivity, 25.0)
        if factors["data_sensitivity"]:
            codes.append("SENSITIVE_DATA_ACCESS")
        scope = str(features.get("scope") or "bounded").lower()
        factors["access_scope"] = {"own_records": 0.0, "aggregate": 3.0, "bounded": 5.0, "all_records": 50.0, "unknown": 25.0}.get(scope, 20.0)
        if factors["access_scope"] >= 25:
            codes.append("BROAD_OR_UNKNOWN_SCOPE")
        factors["behavior_deviation"] = behavior.deviation * 0.35
        factors["workload_drift"] = 10.0 if behavior.drift else 0.0
        # Authorization has already been decided by the deterministic database
        # policy. A behavior score must not duplicate an in-code role matrix.
        score = min(100.0, round(sum(factors.values()), 2))
        level, state = ("HIGH", "block") if score >= 60 else ("MEDIUM", "review") if score >= 25 else ("LOW", "allow")
        return RiskAssessment(score, level, state, tuple(dict.fromkeys(codes)), factors)


class BehaviorRepository:
    def load(
        self, actor_id: str, *, actor_context_id: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        from queryimmune_db.connection import transaction
        with transaction(read_only=True, actor_context_id=actor_context_id) as conn:
            p = conn.execute("""SELECT approved_sample_count, feature_summary, profile_version
                FROM security.behavior_profiles WHERE actor_id=%s AND update_status IN ('active','seeded')
                ORDER BY profile_version DESC LIMIT 1""", (actor_id,)).fetchone()
            rows = conn.execute("""SELECT q.created_at, r.feature_snapshot FROM security.query_logs q
                JOIN security.risk_events r USING(request_id) WHERE q.actor_id=%s
                AND q.created_at >= now() - interval '30 days' ORDER BY q.created_at DESC LIMIT 2000""", (actor_id,)).fetchall()
        profile = None if p is None else {"approved_sample_count": p[0], "feature_summary": p[1], "profile_version": p[2]}
        return profile, [{"created_at": row[0], "feature_snapshot": row[1]} for row in rows]

    def append_profile(
        self,
        actor_id: str,
        expected_version: int,
        sample_count: int,
        summary: Mapping[str, Any],
        *,
        actor_context_id: str,
    ) -> None:
        from queryimmune_db.connection import transaction
        from psycopg.types.json import Jsonb
        with transaction(actor_context_id=actor_context_id) as conn:
            conn.execute("SELECT security.append_behavior_profile(%s,%s::integer,%s::integer,%s::numeric,%s::timestamptz,%s::timestamptz,%s::jsonb)",
                (actor_id, expected_version, sample_count, round(min(0.95, sample_count / 30), 2), datetime.now(timezone.utc)-timedelta(days=90),
                 datetime.now(timezone.utc), Jsonb(dict(summary))))

    def eligible_sample_count(self, actor_id: str, *, actor_context_id: str) -> int:
        from queryimmune_db.connection import transaction
        with transaction(read_only=True, actor_context_id=actor_context_id) as conn:
            row = conn.execute("""SELECT count(*) FROM security.query_logs q
                JOIN security.risk_events r USING(request_id) WHERE q.actor_id=%s
                AND q.decision='allow' AND q.execution_status='succeeded' AND q.created_at >= now()-interval '90 days'
                AND coalesce(r.feature_snapshot->>'risk_level','')='LOW' AND r.risk_score < 25
                AND coalesce(r.feature_snapshot->'risk_reasons', '[]'::jsonb) <@ '["BASELINE_WARMUP","LEGITIMATE_WORKLOAD_DRIFT"]'::jsonb""", (actor_id,)).fetchone()
        return int(row[0])


class BehaviorAwarePipeline:
    """Behavior can increase scrutiny only; existing hard denials/reviews pass through unchanged."""
    def __init__(self, base: RequestPipeline, repository: BehaviorRepository | None = None):
        self.base = base
        self.repository = repository or BehaviorRepository()
        self.analyzer = BehaviorAnalyzer()
        self.risk = ExplainableRiskEngine()

    def decide(self, request: ExecutionRequest, actor: Actor) -> Decision:
        base = self.base.decide(request, actor)
        # Preserve deterministic REVIEW/BLOCK outcomes exactly; learned scores never relax or replace them.
        if base.state != "allow":
            return base
        try:
            profile, history = self.repository.load(actor.user_id, actor_context_id=actor.user_id)
        except (psycopg.Error, RuntimeError):
            return Decision("review", ("BEHAVIOR_CONTEXT_UNAVAILABLE",), 50.0,
                "Behavioral context is unavailable; request requires review", "phase4-behavior-v1",
                base.model_version, {**base.feature_snapshot, "risk_level": "MEDIUM"})
        features = dict(base.feature_snapshot)
        features.setdefault("action", request.action)
        features.setdefault("operation", "read")
        features.setdefault("role_context", sorted(actor.roles))
        behavior = self.analyzer.analyze(features, profile, history)
        assessed = self.risk.assess(features, behavior)
        snapshot = {**features, "behavior": {"deviation": behavior.deviation, "similarity": behavior.similarity,
            "drift": behavior.drift, "drift_kind": behavior.drift_kind, "sample_count": behavior.sample_count},
            "risk_level": assessed.level, "risk_factors": dict(assessed.factors), "risk_reasons": list(assessed.reasons),
            "profile_version": (profile or {}).get("profile_version")}
        return Decision(assessed.state, assessed.reasons, assessed.score, "Risk assessed from policy and workload context",
            "phase4-behavior-v1", base.model_version, snapshot,
            (profile or {}).get("profile_version"))


class ControlledProfileUpdater:
    """Persist profiles only after LOW risk, successful execution, and the DB sample gate."""
    def __init__(self, repository: BehaviorRepository | None = None):
        self.repository = repository or BehaviorRepository()

    def observe_success(
        self,
        actor_id: str,
        features: Mapping[str, Any],
        result_size: int | None = None,
        *,
        actor_context_id: str,
    ) -> None:
        if features.get("risk_level") != "LOW" or set(features.get("risk_reasons") or []) - {"BASELINE_WARMUP", "LEGITIMATE_WORKLOAD_DRIFT"}:
            return
        # Gateway results are capped at 100 rows. Refuse impossible/out-of-policy
        # values here as defense in depth; the database function independently
        # checks the value against audited successful query_logs.
        if result_size is not None and (not isinstance(result_size, int) or result_size < 0 or result_size > 100):
            return
        profile, _ = self.repository.load(actor_id, actor_context_id=actor_context_id)
        version = int((profile or {}).get("profile_version") or 0)
        count = self.repository.eligible_sample_count(actor_id, actor_context_id=actor_context_id)
        if count < 10 or count % 5:
            return
        prior = dict((profile or {}).get("feature_summary") or {})
        def merge(key: str, value: Any) -> list[str]:
            values = _set(prior.get(key))
            values.update(_set(value) if isinstance(value, (list, tuple, set)) else ({str(value).lower()} if value else set()))
            return sorted(values)[:32]
        now = datetime.now(timezone.utc)
        hour = str(now.hour)
        hours = _set(prior.get("active_hours")); hours.add(hour)
        prior.update({"actions": merge("actions", features.get("action", "")),
                      "tables": merge("tables", features.get("tables", [])),
                      "columns": merge("columns", features.get("columns", [])),
                      "operations": merge("operations", features.get("operation", "read")),
                      "scopes": merge("scopes", features.get("scope", "unknown")),
                      "sensitivities": merge("sensitivities", features.get("sensitivity", "standard")),
                      "roles": merge("roles", features.get("role_context", [])),
                      "signatures": merge("signatures", features.get("semantic_signature") or features.get("action", "")),
                      "active_hours": sorted(hours)[:24],
                      "query_structure": merge("query_structure", features.get("semantic_signature", "")),
                      "frequency_sample_count": count})
        if result_size is not None:
            prior["result_size_max"] = max(int(prior.get("result_size_max", 0)), result_size)
        self.repository.append_profile(
            actor_id, version, count, prior, actor_context_id=actor_context_id
        )

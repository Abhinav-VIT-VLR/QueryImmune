from __future__ import annotations

import hashlib
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from queryimmune_db.ai.intent import IntentFilter, MismatchReason, QueryIntent, SQLAnalysis, ValidationResult
from queryimmune_db.ai.privacy import redact_direct_identifiers


ACTION_TABLES = {
    "appointments.mine": {"appointments", "patients", "doctors"},
    "appointments.by_status": {"appointments", "doctors"},
    "billing.summary": {"billing"},
    "appointments.cardiology_today": {"appointments", "doctors"},
}
_TEMPLATE_SHAPES = {
    "appointments.mine": (
        {"scheduled_at", "status", "reason_category", "synthetic_patient_number", "specialty"},
        set(), set(), {("scheduled_at", "desc")},
    ),
    "appointments.by_status": (
        {"status", "specialty"}, {"count"}, {"status", "specialty"}, {("appointment_count", "desc")},
    ),
    "billing.summary": (
        {"status", "currency", "amount"}, {"count", "sum"}, {"status", "currency"},
        {("status", "asc"), ("currency", "asc")},
    ),
    "appointments.cardiology_today": ({"patient_id"}, {"count"}, set(), set()),
}
_JOIN_SHAPES = {
    "appointments.mine": {
        ("appointments.patient_id", "patients.id"),
        ("appointments.doctor_id", "doctors.id"),
    },
    "appointments.by_status": {("appointments.doctor_id", "doctors.id")},
    "billing.summary": set(),
    "appointments.cardiology_today": {("appointments.doctor_id", "doctors.id")},
}
SENSITIVE_TABLES = {"medical_records", "risk_events", "users"}
SENSITIVE_COLUMNS = {"date_of_birth", "summary", "amount", "external_subject"}
_COMPARISONS = {
    exp.EQ: "eq",
    exp.GTE: "gte",
    exp.GT: "gt",
    exp.LTE: "lte",
    exp.LT: "lt",
}
_AGGREGATES = {
    exp.Count: "count",
    exp.Sum: "sum",
    exp.Avg: "avg",
    exp.Min: "min",
    exp.Max: "max",
}
_COLUMN_ALLOWLIST = {
    "appointments.mine": {
        "appointments": {"id", "patient_id", "doctor_id", "scheduled_at", "status", "reason_category"},
        "patients": {"id", "synthetic_patient_number"},
        "doctors": {"id", "user_id", "specialty"},
    },
    "appointments.by_status": {
        "appointments": {"id", "doctor_id", "scheduled_at", "status"},
        "doctors": {"id", "specialty"},
    },
    "billing.summary": {
        "billing": {"status", "currency", "created_at", "amount"},
    },
    "appointments.cardiology_today": {
        "appointments": {"patient_id", "doctor_id", "scheduled_at", "status"},
        "doctors": {"id", "specialty"},
    },
}

_CARDIOLOGY_TODAY_SQL = """SELECT COUNT(DISTINCT a.patient_id) AS patient_count
FROM healthcare.appointments AS a
JOIN healthcare.doctors AS d ON d.id = a.doctor_id
WHERE d.specialty = 'Cardiology' AND a.status = 'completed'
AND a.scheduled_at::date = CURRENT_DATE LIMIT 1"""


def _reason(code: str, message: str, expected: str | None = None, actual: str | None = None) -> MismatchReason:
    return MismatchReason(code=code, message=message, expected=expected, actual=actual)


def _normal_name(value: str) -> str:
    return value.rsplit(".", 1)[-1].casefold()


def _literal_value(node: exp.Expression) -> str | int | float | bool | None:
    if isinstance(node, exp.Placeholder):
        return node.name.casefold() or "?"
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Literal):
        if node.is_string:
            return str(node.this)
        try:
            return int(node.this)
        except (TypeError, ValueError):
            try:
                return float(node.this)
            except (TypeError, ValueError):
                return str(node.this)
    raise ValueError("filter right-hand side must be a literal or bind placeholder")


def _filter_predicates(node: exp.Expression | None) -> list[tuple[str, str, str | int | float | bool | None]]:
    if node is None:
        return []
    if isinstance(node, exp.And):
        return _filter_predicates(node.this) + _filter_predicates(node.expression)
    for expression_type, operator in _COMPARISONS.items():
        if isinstance(node, expression_type):
            column = node.this
            value = node.expression
            if not isinstance(column, exp.Column):
                # Literal-on-left comparisons are normalized only where unambiguous.
                if isinstance(value, exp.Column):
                    column, value = value, column
                    operator = {"gt": "lt", "gte": "lte", "lt": "gt", "lte": "gte"}.get(operator, operator)
                else:
                    raise ValueError("filter must compare a column to a literal")
            return [(_normal_name(column.name), operator, _literal_value(value))]
    raise ValueError("only simple AND filters are supported")


def _intent_filter_key(item: IntentFilter) -> tuple[str, str, str | int | float | bool]:
    return _normal_name(item.column), item.operator, item.value


class IntentSqlValidator:
    """Compare model output through a PostgreSQL AST; never executes or rewrites candidate SQL."""

    def validate(self, request_text: str, intent: QueryIntent, candidate_sql: str) -> ValidationResult:
        reasons: list[MismatchReason] = []

        def add(code: str, message: str, expected: str | None = None, actual: str | None = None) -> None:
            if code not in {item.code for item in reasons}:
                reasons.append(_reason(code, message, expected, actual))

        requested_families = _request_families(request_text)
        intent_family = "appointments" if intent.action.startswith("appointments.") else "billing" if intent.action.startswith("billing.") else "unsupported"
        if len(requested_families) > 1 or (requested_families and intent_family not in requested_families):
            add("REQUEST_ENTITY_MISMATCH", "Structured action does not match the entity requested by the user", ",".join(sorted(requested_families)), intent_family)
        if _requests_mutation(request_text):
            add("REQUEST_OPERATION_MISMATCH", "The request describes a write operation unsupported by this gateway", "read-only", "write requested")
        if intent.operation != "read":
            add("UNSUPPORTED_OPERATION", "The execution gateway accepts read intents only", "read", intent.operation)
        scrubbed_request = redact_direct_identifiers(request_text)
        if "[REDACTED_" in scrubbed_request:
            add("UNSUPPORTED_FILTER", "Direct identifiers and patient-specific filters are not supported by this gateway")
        requested_dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", request_text))
        intent_dates = {str(item.value) for item in intent.filters if item.column.rsplit(".", 1)[-1] in {"scheduled_at", "created_at"}}
        if requested_dates and not requested_dates.issubset(intent_dates):
            add("FILTER_MISMATCH", "Explicit dates in the user request must be preserved in the intent", ",".join(sorted(requested_dates)), ",".join(sorted(intent_dates)))
        limit_match = re.search(r"\b(?:top|limit(?:\s+of)?)\s+(\d+)\b|\b(?:first|last)\s+(\d+)\s+(?:appointments?|records?|invoices?|results?)\b", request_text, re.IGNORECASE)
        if limit_match:
            requested_limit = int(next(value for value in limit_match.groups() if value is not None))
            if requested_limit != intent.limit:
                add("LIMIT_MISMATCH", "Explicit result limit in the user request must be preserved", str(requested_limit), str(intent.limit))
        if intent.scope != "aggregate" and re.search(r"\b(all|every|entire)\s+(?:of\s+the\s+)?(?:appointments?|records?|invoices?|patients?)\b", request_text, re.IGNORECASE):
            add("SCOPE_MISMATCH", "An unbounded extraction request is not supported")

        if not candidate_sql.strip() or len(candidate_sql.encode("utf-8")) > 16384:
            return ValidationResult(valid=False, reasons=[_reason("MALFORMED_SQL", "SQL candidate is empty or exceeds the size limit")])
        try:
            statements = sqlglot.parse(candidate_sql, read="postgres")
        except ParseError:
            return ValidationResult(valid=False, reasons=[_reason("MALFORMED_SQL", "SQL candidate could not be parsed as PostgreSQL")])
        if len(statements) != 1 or statements[0] is None:
            return ValidationResult(valid=False, reasons=[_reason("MULTIPLE_STATEMENTS", "Exactly one SQL statement is required")])

        tree = statements[0]
        operation = _operation(tree)
        if intent.action == "appointments.cardiology_today":
            expected_tree = sqlglot.parse_one(_CARDIOLOGY_TODAY_SQL, read="postgres")
            if tree.dump() != expected_tree.dump():
                add("EXECUTION_TEMPLATE_MISMATCH", "Cardiology-today count must match its fixed, bounded aggregate template")
            if (intent.operation != "read" or set(intent.tables) != {"appointments", "doctors"}
                    or set(intent.columns) != {"patient_id"} or set(intent.aggregations) != {"count"}
                    or intent.filters or intent.group_by or intent.ordering
                    or intent.scope != "aggregate" or intent.limit != 1):
                add("INTENT_TEMPLATE_MISMATCH", "Intent does not match the fixed Cardiology-today aggregate")
        if intent.operation == "read" and operation in {"delete", "update", "insert"}:
            add("READ_INTENT_MUTATION", "A read intent produced a data-changing statement", "read", operation)
        elif operation != intent.operation:
            add("OPERATION_MISMATCH", "SQL operation does not match the structured intent", intent.operation, operation)
        if not isinstance(tree, exp.Select):
            return ValidationResult(valid=False, reasons=reasons or [_reason("UNSUPPORTED_OPERATION", "Only SELECT candidates are supported", "select", operation)])

        if tree.args.get("with") is not None or tree.args.get("into") is not None or tree.args.get("locks"):
            add("UNSUPPORTED_SQL_SHAPE", "CTEs, SELECT INTO, and row locks are not supported")
        if tree.args.get("offset") is not None:
            add("SCOPE_MISMATCH", "Offset pagination is not supported by the execution gateway")
        if tree.find(exp.Subquery) is not None or tree.find(exp.Union) is not None:
            add("UNSUPPORTED_SQL_SHAPE", "Subqueries and set operations are not supported")
        if any(isinstance(item, exp.Star) for item in tree.expressions) or any(
            isinstance(item, exp.Column) and item.is_star for item in tree.expressions
        ):
            add("WILDCARD_PROJECTION", "Wildcard projections can expose unrequested columns")
        if any(isinstance(fn, exp.Anonymous) for fn in tree.find_all(exp.Anonymous)):
            add("UNSAFE_FUNCTION", "Unrecognized SQL functions are not supported")
        for join in tree.args.get("joins") or []:
            if join.args.get("on") is None:
                add("UNSAFE_JOIN", "Every join must have an explicit ON predicate")

        table_nodes = list(tree.find_all(exp.Table))
        table_names = {table.name.casefold() for table in table_nodes}
        if not _joins_match_template(tree, intent.action):
            add("JOIN_MISMATCH", "Join relationships do not match the approved execution template")
        for table in table_nodes:
            schema = table.db.casefold()
            if table.catalog or (schema and schema != "healthcare"):
                add("UNRELATED_ENTITY", "Only allowlisted healthcare relations are supported", "healthcare", schema or table.catalog)
        if table_names.intersection(SENSITIVE_TABLES):
            add("SENSITIVE_DATA_ACCESS", "Candidate SQL touches a restricted identity or security table")

        required_tables = ACTION_TABLES.get(intent.action)
        if required_tables is None:
            add("UNSUPPORTED_ACTION", "Structured intent selected an unsupported action")
        else:
            if set(intent.tables) != required_tables:
                add("INTENT_ENTITY_MISMATCH", "Intent entities do not match the selected action", ",".join(sorted(required_tables)), ",".join(sorted(set(intent.tables))))
            if table_names != required_tables:
                add("ENTITY_MISMATCH", "SQL relations do not match the selected action", ",".join(sorted(required_tables)), ",".join(sorted(table_names)))

        selected_columns: set[str] = set()
        selected_aliases: set[str] = set()
        for projection in tree.expressions:
            selected_columns.update(column.name.casefold() for column in projection.find_all(exp.Column))
            if isinstance(projection, exp.Alias):
                selected_aliases.add(projection.alias.casefold())
        all_columns = {column.name.casefold() for column in tree.find_all(exp.Column)}
        if all_columns.intersection(SENSITIVE_COLUMNS):
            # Billing amount is sensitive only when the intent explicitly expects it.
            sensitive_access = all_columns.intersection(SENSITIVE_COLUMNS)
            if not {"sensitive", "restricted"}.intersection(intent.sensitivity_expectations):
                add("SENSITIVE_DATA_ACCESS", "Candidate SQL references a sensitive field absent from the intent", None, ",".join(sorted(sensitive_access)))
        allowlist = _COLUMN_ALLOWLIST.get(intent.action, {})
        allowed_columns = set().union(*allowlist.values()) if allowlist else set()
        unknown_columns = all_columns - allowed_columns - selected_aliases
        if unknown_columns:
            add("UNRELATED_COLUMN", "Candidate SQL references columns outside the selected action", ",".join(sorted(allowed_columns)), ",".join(sorted(unknown_columns)))
        expected_columns = {_normal_name(column) for column in intent.columns}
        if selected_columns != expected_columns:
            add("COLUMN_MISMATCH", "Projected columns do not match the structured intent", ",".join(sorted(expected_columns)), ",".join(sorted(selected_columns)))

        actual_aggregations: set[str] = set()
        for node_type, name in _AGGREGATES.items():
            if any(True for _ in tree.find_all(node_type)):
                actual_aggregations.add(name)
        if actual_aggregations != set(intent.aggregations):
            add("AGGREGATION_MISMATCH", "SQL aggregates do not match the structured intent", ",".join(sorted(intent.aggregations)), ",".join(sorted(actual_aggregations)))

        actual_grouping = {_normal_name(column.name) for group in tree.find_all(exp.Group) for column in group.find_all(exp.Column)}
        expected_grouping = {_normal_name(column) for column in intent.group_by}
        if actual_grouping != expected_grouping:
            add("GROUPING_MISMATCH", "SQL grouping does not match the structured intent", ",".join(sorted(expected_grouping)), ",".join(sorted(actual_grouping)))

        actual_order: set[tuple[str, str]] = set()
        for order in tree.find_all(exp.Order):
            for ordered in order.expressions:
                if isinstance(ordered, exp.Ordered) and isinstance(ordered.this, exp.Column):
                    actual_order.add((_normal_name(ordered.this.name), "desc" if ordered.args.get("desc") else "asc"))
        expected_order = {(_normal_name(item.column), item.direction) for item in intent.ordering}
        if actual_order != expected_order:
            add("ORDERING_MISMATCH", "SQL ordering does not match the structured intent", str(sorted(expected_order)), str(sorted(actual_order)))

        actual_limit = _limit_value(tree)
        if actual_limit != intent.limit:
            add("LIMIT_MISMATCH", "SQL limit must equal the requested bounded scope", str(intent.limit), str(actual_limit) if actual_limit is not None else "unbounded")
        if intent.scope in {"all_records", "unknown"}:
            add("SCOPE_MISMATCH", "Unbounded or unknown scope cannot pass the execution gateway", "bounded or own_records or aggregate", intent.scope)
        if intent.action in ACTION_TABLES and intent.scope != _required_scope(intent.action):
            add("SCOPE_MISMATCH", "Intent scope does not match the selected action", _required_scope(intent.action), intent.scope)
        if intent.action == "appointments.mine" and not _has_actor_scope(tree):
            add("SCOPE_MISMATCH", "Doctor appointment query must bind the authenticated actor", "doctors.user_id = :actor_id", "missing authenticated scope")
        if intent.scope == "aggregate" and not actual_aggregations:
            add("AGGREGATION_MISMATCH", "Aggregate scope requires an aggregate expression")

        expected_shape = _TEMPLATE_SHAPES.get(intent.action)
        if expected_shape:
            expected_projection, expected_aggregations, expected_grouping, expected_order = expected_shape
            if (selected_columns, actual_aggregations, actual_grouping, actual_order) != (
                expected_projection, expected_aggregations, expected_grouping, expected_order
            ):
                add("EXECUTION_TEMPLATE_MISMATCH", "Candidate shape differs from the only executable query template")

        try:
            actual_filters = [] if intent.action == "appointments.cardiology_today" else _filter_predicates(tree.args.get("where").this if tree.args.get("where") else None)
        except ValueError:
            actual_filters = []
            add("UNSUPPORTED_FILTER", "Only simple supported AND filters are allowed")
        if intent.action == "appointments.mine":
            actual_filters = [item for item in actual_filters if not (item[0] == "user_id" and item[1] == "eq" and item[2] == "actor_id")]
        expected_filters = [_intent_filter_key(item) for item in intent.filters]
        allowed_filters = {
            "appointments.mine": {("scheduled_at", "gte")},
            "appointments.by_status": {("scheduled_at", "gte")},
            "billing.summary": {("created_at", "gte")},
        }.get(intent.action, set())
        if any((column, operator) not in allowed_filters for column, operator, _ in expected_filters + actual_filters):
            add("UNSUPPORTED_FILTER", "Filter is outside the execution gateway's supported parameter set")
        for column, operator, value in expected_filters + actual_filters:
            if (column, operator) in allowed_filters:
                from datetime import date
                try:
                    date.fromisoformat(str(value))
                except ValueError:
                    add("UNSUPPORTED_FILTER", "Date filter must use an ISO calendar date")
        if set(actual_filters) != set(expected_filters):
            expected_labels = ",".join(f"{c}:{op}" for c, op, _ in expected_filters)
            actual_labels = ",".join(f"{c}:{op}" for c, op, _ in actual_filters)
            add("FILTER_MISMATCH", "SQL filters do not match the structured intent", expected_labels, actual_labels)

        canonical = tree.sql(dialect="postgres", pretty=False)
        analysis = SQLAnalysis(
            operation=operation,
            tables=sorted(table_names),
            columns=sorted(all_columns),
            projected_columns=sorted(selected_columns),
            filters=[IntentFilter(column=c, operator=op, value=value) for c, op, value in actual_filters],
            aggregations=sorted(actual_aggregations),
            group_by=sorted(actual_grouping),
            ordering=[{"column": c, "direction": d} for c, d in sorted(actual_order)],
            limit=actual_limit,
            canonical_fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        return ValidationResult(valid=not reasons, reasons=reasons, analysis=analysis)


def _operation(tree: exp.Expression) -> str:
    for expression_type, operation in ((exp.Select, "read"), (exp.Insert, "insert"), (exp.Update, "update"), (exp.Delete, "delete")):
        if isinstance(tree, expression_type):
            return operation
    return "unsupported"


def _limit_value(tree: exp.Select) -> int | None:
    limit = tree.args.get("limit")
    if limit is None or not isinstance(limit.expression, exp.Literal) or limit.expression.is_string:
        return None
    try:
        value = int(limit.expression.this)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _has_actor_scope(tree: exp.Select) -> bool:
    where = tree.args.get("where")
    if where is None:
        return False
    aliases = {table.alias_or_name.casefold(): table.name.casefold() for table in tree.find_all(exp.Table)}
    for node in where.find_all(exp.EQ):
        column, value = node.this, node.expression
        if (
            isinstance(column, exp.Column) and column.name.casefold() == "user_id"
            and aliases.get(column.table.casefold()) == "doctors"
            and isinstance(value, exp.Placeholder) and value.name.casefold() == "actor_id"
        ):
            return True
    return False


def _joins_match_template(tree: exp.Select, action: str) -> bool:
    expected = _JOIN_SHAPES.get(action)
    if expected is None:
        return False
    aliases = {table.alias_or_name.casefold(): table.name.casefold() for table in tree.find_all(exp.Table)}
    actual: set[tuple[str, str]] = set()
    for join in tree.args.get("joins") or []:
        if join.args.get("side") or (join.args.get("kind") or "").casefold() not in {"", "inner"}:
            return False
        predicate = join.args.get("on")
        if not isinstance(predicate, exp.EQ) or not isinstance(predicate.this, exp.Column) or not isinstance(predicate.expression, exp.Column):
            return False
        left, right = predicate.this, predicate.expression
        left_table = aliases.get(left.table.casefold())
        right_table = aliases.get(right.table.casefold())
        if not left_table or not right_table:
            return False
        actual.add(tuple(sorted((f"{left_table}.{left.name.casefold()}", f"{right_table}.{right.name.casefold()}"))))
    expected_normalized = {tuple(sorted(edge)) for edge in expected}
    return actual == expected_normalized


def _required_scope(action: str) -> str:
    return "own_records" if action == "appointments.mine" else "aggregate"


def _request_families(text: str) -> set[str]:
    normalized = text.casefold()
    families: set[str] = set()
    if re.search(r"\b(appointments?|visits?|visited|schedules?)\b", normalized):
        families.add("appointments")
    if re.search(r"\b(billing|bills?|invoices?|charges?)\b", normalized):
        families.add("billing")
    if re.search(r"\bmedical\s+records?|clinical\s+records?|diagnos(?:is|es)|prescriptions?|lab\s+results?\b", normalized):
        families.add("medical_records")
    return families


def _requests_mutation(text: str) -> bool:
    return bool(re.search(r"\b(delete|remove|erase|update|modify|change|insert|create|alter|drop)\b", text.casefold()))

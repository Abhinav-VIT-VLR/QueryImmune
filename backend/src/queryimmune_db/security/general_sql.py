from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from queryimmune_db.ai.intent import IntentFilter, MismatchReason, QueryIntent, SQLAnalysis, ValidationResult
from queryimmune_db.security.schema import GENERAL_QUERY_ACTIONS, GENERAL_QUERY_RELATIONS, GENERAL_QUERY_RELATIONSHIPS


SAFE_COLUMNS = {name: metadata["columns"] for name, metadata in GENERAL_QUERY_RELATIONS.items()}
RELATIONSHIPS = {frozenset(edge) for edge in GENERAL_QUERY_RELATIONSHIPS}
AGGREGATES = {exp.Count: "count", exp.Sum: "sum", exp.Avg: "avg", exp.Min: "min", exp.Max: "max"}
COMPARISONS = {exp.EQ: "eq", exp.GTE: "gte", exp.GT: "gt", exp.LTE: "lte", exp.LT: "lt"}
MAX_SQL_BYTES = 16_384
MAX_AST_NODES = 200
MAX_AST_DEPTH = 16
MAX_JOINS = 2
MAX_ROWS = 100


def _reason(code: str, message: str) -> MismatchReason:
    return MismatchReason(code=code, message=message)


def _col_key(column: exp.Column) -> str:
    return f"{column.table.casefold()}.{column.name.casefold()}" if column.table else column.name.casefold()


def _literal(node: exp.Expression):
    if isinstance(node, exp.Placeholder):
        return f":{node.name.casefold()}"
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Literal):
        if node.is_string:
            return str(node.this)
        try:
            return int(node.this)
        except (TypeError, ValueError):
            return float(node.this)
    raise ValueError


def _filters(node: exp.Expression | None, aliases: dict[str, str]) -> list[tuple[str, str, object]]:
    if node is None:
        return []
    if isinstance(node, exp.And):
        return _filters(node.this, aliases) + _filters(node.expression, aliases)
    for typ, op in COMPARISONS.items():
        if isinstance(node, typ) and isinstance(node.this, exp.Column):
            column = node.this
            relation = aliases.get(column.table.casefold(), "")
            key = f"{relation}.{column.name.casefold()}" if relation else column.name.casefold()
            return [(key, op, _literal(node.expression))]
    raise ValueError


def _limit(tree: exp.Select) -> int | None:
    item = tree.args.get("limit")
    if item is None or not isinstance(item.expression, exp.Literal) or item.expression.is_string:
        return None
    try:
        value = int(item.expression.this)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _actor_scoped(tree: exp.Select, aliases: dict[str, str]) -> bool:
    where = tree.args.get("where")
    if where is None:
        return False
    for item in where.find_all(exp.EQ):
        left, right = item.this, item.expression
        if isinstance(left, exp.Column) and isinstance(right, exp.Placeholder):
            if aliases.get(left.table.casefold()) in {"doctors", "general_doctor_directory"} and left.name.casefold() == "user_id" and right.name.casefold() == "actor_id":
                return True
        if isinstance(right, exp.Column) and isinstance(left, exp.Placeholder):
            if aliases.get(right.table.casefold()) in {"doctors", "general_doctor_directory"} and right.name.casefold() == "user_id" and left.name.casefold() == "actor_id":
                return True
    return False


def _join_edges(tree: exp.Select, aliases: dict[str, str]) -> set[frozenset[str]] | None:
    edges: set[frozenset[str]] = set()
    for join in tree.args.get("joins") or []:
        if join.args.get("side") not in (None, "", "LEFT") or join.args.get("kind") not in (None, "", "INNER"):
            return None
        on = join.args.get("on")
        if not isinstance(on, exp.EQ) or not isinstance(on.this, exp.Column) or not isinstance(on.expression, exp.Column):
            return None
        left = on.this
        right = on.expression
        if not left.table or not right.table:
            return None
        edge = frozenset((f"{aliases.get(left.table.casefold(), '')}.{left.name.casefold()}", f"{aliases.get(right.table.casefold(), '')}.{right.name.casefold()}"))
        if edge not in RELATIONSHIPS:
            return None
        edges.add(edge)
    return edges


class GeneralSelectValidator:
    """Fail-closed AST validation for the isolated safe appointments query path."""

    def validate(self, request_text: str, intent: QueryIntent, sql: str) -> ValidationResult:
        reasons: list[MismatchReason] = []

        def add(code: str, message: str) -> None:
            if code not in {reason.code for reason in reasons}:
                reasons.append(_reason(code, message))

        if intent.request_status != "supported":
            add("UNSUPPORTED_OR_AMBIGUOUS_REQUEST", "Only requests classified as supported can be considered for execution")
        if intent.action not in set(GENERAL_QUERY_ACTIONS.values()):
            add("UNSUPPORTED_ACTION", "General queries are limited to the approved appointments actions")
        if intent.operation != "read":
            add("UNSUPPORTED_OPERATION", "General execution accepts read-only requests")
        if _requests_mutation(request_text):
            add("REQUEST_OPERATION_MISMATCH", "Write or destructive intent cannot enter general read execution")
        if re.search(r"\b(another|other)\s+(?:doctor|provider)|\bfor\s+(?:dr\.?|doctor)\s+[A-Z]", request_text, re.I):
            add("SCOPE_MISMATCH", "This query path cannot select another provider's records")
        request_has_own_scope = bool(re.search(r"\b(my|mine|me|i)\b", request_text, re.I))
        if request_has_own_scope and intent.action == GENERAL_QUERY_ACTIONS["aggregate"]:
            add("SCOPE_MISMATCH", "A request explicitly scoped to the authenticated actor cannot be broadened to a shared aggregate")
        if re.search(r"\b(billing|invoices?|charges?)\b", request_text, re.I):
            add("REQUEST_ENTITY_MISMATCH", "Billing requests cannot be rewritten as general appointments queries")
        broad_request = bool(re.search(r"\b(all|every|entire)\s+(?:of\s+the\s+)?(?:appointments?|visits?|patients?)\b", request_text, re.I))
        if broad_request:
            add("SCOPE_MISMATCH", "Unbounded extraction requests are not supported by this query path")
        grouped_doctor_request = bool(re.search(r"\b(each|per)\s+doctor\b|\b(which|what) doctors?\b.*\b(most|fewest|top)\b", request_text, re.I))
        if grouped_doctor_request and intent.action != GENERAL_QUERY_ACTIONS["aggregate"]:
            add("SCOPE_MISMATCH", "A comparison across doctors must be represented as an authorized aggregate")
        if re.search(r"\b(how many|count|number of)\b", request_text, re.I) and not intent.aggregations:
            add("REQUEST_AGGREGATION_MISMATCH", "A requested count must be represented as an aggregate")
        if grouped_doctor_request and "display_name" not in {column.rsplit(".", 1)[-1] for column in intent.group_by}:
            add("REQUEST_GROUPING_MISMATCH", "Doctor comparisons must group by the safe clinician display name")
        if not sql.strip() or len(sql.encode("utf-8")) > MAX_SQL_BYTES:
            return ValidationResult(valid=False, reasons=[_reason("MALFORMED_SQL", "SQL is empty or exceeds its byte limit")])
        try:
            statements = sqlglot.parse(sql, read="postgres")
        except SqlglotError:
            return ValidationResult(valid=False, reasons=[_reason("MALFORMED_SQL", "SQL candidate could not be parsed as PostgreSQL")])
        if len(statements) != 1 or statements[0] is None:
            return ValidationResult(valid=False, reasons=[_reason("MULTIPLE_STATEMENTS", "Exactly one statement is permitted")])
        tree = statements[0]
        if not isinstance(tree, exp.Select):
            add("UNSUPPORTED_OPERATION", "Only SELECT is eligible for general execution")
            return ValidationResult(valid=False, reasons=reasons)
        node_count, max_depth = _tree_complexity(tree)
        if node_count > MAX_AST_NODES or max_depth > MAX_AST_DEPTH:
            add("QUERY_COMPLEXITY", "SQL AST exceeds the supported complexity bound")
        if tree.args.get("with") or tree.args.get("into") or tree.args.get("locks") or tree.args.get("offset"):
            add("UNSUPPORTED_SQL_SHAPE", "CTEs, SELECT INTO, locks, and offset pagination are not supported")
        if tree.find(exp.Subquery) or tree.find(exp.Union) or tree.find(exp.Command):
            add("UNSUPPORTED_SQL_SHAPE", "Subqueries and set operations are not supported")
        if len(tree.args.get("joins") or []) > MAX_JOINS:
            add("QUERY_COMPLEXITY", "Too many joins")
        if any(isinstance(item, exp.Star) or (isinstance(item, exp.Column) and item.is_star) for item in tree.expressions):
            add("WILDCARD_PROJECTION", "Wildcard projections are not allowed")
        if any(isinstance(node, exp.Anonymous) for node in tree.find_all(exp.Anonymous)):
            add("UNSAFE_FUNCTION", "Unrecognized functions are not allowed")

        table_nodes = list(tree.find_all(exp.Table))
        aliases: dict[str, str] = {}
        for table in table_nodes:
            aliases[table.alias_or_name.casefold()] = table.name.casefold()
            if table.db.casefold() != "healthcare" or table.catalog:
                add("UNAUTHORIZED_RELATION", "Only explicitly allowlisted healthcare relations are permitted")
        tables = {table.name.casefold() for table in table_nodes}
        if not tables or not tables.issubset(SAFE_COLUMNS):
            add("UNAUTHORIZED_RELATION", "The query references a relation outside the safe query allowlist")
        if set(intent.tables) != tables:
            add("INTENT_ENTITY_MISMATCH", "SQL relations differ from the structured intent")
        if "restricted" in intent.sensitivity_expectations or "sensitive" in intent.sensitivity_expectations:
            add("SENSITIVITY_EXPANSION", "Sensitive or restricted requests are not eligible for the general read path")
        if len(table_nodes) != len(tables) or len(tree.args.get("joins") or []) != max(0, len(table_nodes) - 1):
            add("UNSAFE_JOIN", "Every relation must appear once in the approved join graph")
        from_node = tree.args.get("from_")
        if from_node is None or not isinstance(from_node.this, exp.Table) or from_node.this.name.casefold() != "appointments":
            add("UNAUTHORIZED_RELATION", "General queries must start from appointments")
        if intent.action == GENERAL_QUERY_ACTIONS["own"] and "appointments" not in tables:
            add("INTENT_ENTITY_MISMATCH", "Actor-scoped general requests must query appointments")
        if intent.action == GENERAL_QUERY_ACTIONS["aggregate"] and ("appointments" not in tables or "patients" in tables):
            add("SENSITIVITY_EXPANSION", "Cross-actor aggregate queries cannot expose patient-level relations")
        if len({table.alias_or_name.casefold() for table in table_nodes}) != len(table_nodes):
            add("UNSUPPORTED_SQL_SHAPE", "Relation aliases must be unique")
        edges = _join_edges(tree, aliases)
        if edges is None:
            add("UNSAFE_JOIN", "Join type or relationship is not allowlisted")

        all_columns: set[str] = set()
        projection_aliases = {item.alias.casefold() for item in tree.expressions if isinstance(item, exp.Alias)}
        for column in tree.find_all(exp.Column):
            if column.is_star:
                continue
            relation = aliases.get(column.table.casefold()) if column.table else None
            if relation is None and column.name.casefold() in projection_aliases:
                all_columns.add(column.name.casefold())
                continue
            if not relation or column.name.casefold() not in SAFE_COLUMNS.get(relation, set()):
                add("UNAUTHORIZED_COLUMN", "Column is not available in the general-query allowlist")
            all_columns.add(_col_key(column))
        if intent.action == GENERAL_QUERY_ACTIONS["own"] and not _actor_scoped(tree, aliases):
            add("MISSING_ACTOR_SCOPE", "Actor-scoped queries must bind doctors.user_id to the trusted actor placeholder")

        aggregate_names = {name for typ, name in AGGREGATES.items() if any(True for _ in tree.find_all(typ))}
        if any(isinstance(fn, exp.Func) and not isinstance(fn, exp.Condition) and type(fn) not in AGGREGATES for fn in tree.find_all(exp.Func)):
            add("UNSAFE_FUNCTION", "Only COUNT, SUM, AVG, MIN, and MAX aggregates are supported")
        actual_grouping = {f"{aliases.get(column.table.casefold(), '')}.{column.name.casefold()}" if column.table else column.name.casefold()
                           for group in tree.find_all(exp.Group) for column in group.find_all(exp.Column)}
        actual_projection: set[str] = set()
        for projection in tree.expressions:
            for column in projection.find_all(exp.Column):
                if not any(column in aggregate.find_all(exp.Column) for typ in AGGREGATES for aggregate in tree.find_all(typ)):
                    relation = aliases.get(column.table.casefold(), "")
                    actual_projection.add(f"{relation}.{column.name.casefold()}" if relation else column.name.casefold())
        intent_projection = {name.rsplit(".", 1)[-1].casefold() for name in intent.columns}
        actual_projection = {value.rsplit(".", 1)[-1] for value in actual_projection}
        if actual_projection.intersection({"user_id", "doctor_id"}):
            add("UNAUTHORIZED_COLUMN", "Internal provider identifiers may be used for scope and joins but not returned")
        if actual_projection != intent_projection:
            add("PROJECTED_COLUMN_MISMATCH", "Projected columns differ from structured intent")
        if aggregate_names != set(intent.aggregations):
            add("AGGREGATION_MISMATCH", "Aggregates differ from structured intent")
        expected_grouping = {name.rsplit(".", 1)[-1].casefold() for name in intent.group_by}
        actual_grouping = {value.rsplit(".", 1)[-1] for value in actual_grouping}
        if actual_grouping != expected_grouping:
            add("GROUPING_MISMATCH", "Grouping differs from structured intent")
        if intent.action == GENERAL_QUERY_ACTIONS["aggregate"] and (not aggregate_names or actual_projection - actual_grouping):
            add("SCOPE_MISMATCH", "Cross-actor queries must return aggregate values and grouping columns only")
        if intent.action == GENERAL_QUERY_ACTIONS["aggregate"]:
            having = tree.args.get("having")
            valid_threshold = False
            if having and isinstance(having.this, exp.GTE):
                left, right = having.this.this, having.this.expression
                valid_threshold = (
                    isinstance(left, exp.Count) and _safe_count_expression(left)
                    and isinstance(right, exp.Literal) and not right.is_string and str(right.this) == "3"
                )
            if not valid_threshold:
                add("AGGREGATE_DISCLOSURE_RISK", "Cross-actor aggregates require a minimum group size of three")
        elif tree.args.get("having"):
            add("UNSUPPORTED_SQL_SHAPE", "HAVING is reserved for the deterministic aggregate disclosure guard")
        aggregate_nodes = [node for node in tree.walk() if type(node) in AGGREGATES]
        if grouped_doctor_request and re.search(r"\bpatients?\b", request_text, re.I):
            if not any(isinstance(node.this, exp.Distinct) and _safe_count_expression(node) for node in tree.find_all(exp.Count)):
                add("REQUEST_AGGREGATION_MISMATCH", "Per-doctor patient totals must count distinct patient IDs")
        if intent.action == GENERAL_QUERY_ACTIONS["aggregate"] and any(
            not isinstance(node, exp.Count) or not _safe_count_expression(node)
            for node in aggregate_nodes
        ):
            add("UNSUPPORTED_AGGREGATION", "Cross-actor aggregation is limited to COUNT(*) or COUNT(DISTINCT patient_id)")
        placeholders = [node.name.casefold() for node in tree.find_all(exp.Placeholder)]
        if any(name != "actor_id" for name in placeholders) or (intent.action == GENERAL_QUERY_ACTIONS["own"] and placeholders.count("actor_id") != 1):
            add("UNSAFE_PARAMETER", "Only the single trusted actor placeholder is supported")

        try:
            actual_filters = _filters(tree.args.get("where").this if tree.args.get("where") else None, aliases)
        except (ValueError, AttributeError):
            actual_filters = []
            add("UNSUPPORTED_FILTER", "Only simple AND comparisons to literals or the trusted actor placeholder are supported")
        if any(column not in {"doctors.user_id", "general_doctor_directory.user_id", "appointments.scheduled_at", "appointments.status", "doctors.specialty", "general_doctor_directory.specialty"}
               for column, _, _ in actual_filters):
            add("UNSUPPORTED_FILTER", "General queries cannot filter on patient or reason-detail fields")
        actor_filters = [item for item in actual_filters if item in {
            ("doctors.user_id", "eq", ":actor_id"),
            ("general_doctor_directory.user_id", "eq", ":actor_id"),
        }]
        if intent.action == GENERAL_QUERY_ACTIONS["own"]:
            if len(actor_filters) != 1:
                add("MISSING_ACTOR_SCOPE", "Exactly one trusted actor-scope predicate is required")
            actual_filters = [item for item in actual_filters if item not in actor_filters]
        expected_filters = sorted((item.column.rsplit(".", 1)[-1].casefold(), item.operator, item.value) for item in intent.filters)
        actual_filters = [((column.rsplit(".", 1)[-1] if column not in {"doctors.user_id", "general_doctor_directory.user_id"} else column), op, value)
                          for column, op, value in actual_filters]
        actual_filter_values = sorted(actual_filters)
        if actual_filter_values != expected_filters:
            add("FILTER_MISMATCH", "SQL filters differ from structured intent")
        intent_filter_set = {(column, operator, str(value).casefold()) for column, operator, value in expected_filters}
        lowered_request = request_text.casefold()
        for status in ("completed", "cancelled", "scheduled", "no_show"):
            if status.replace("_", " ") in lowered_request and ("status", "eq", status) not in intent_filter_set:
                add("REQUEST_FILTER_MISMATCH", "The requested appointment status must be preserved")
        if "cardiology" in lowered_request and ("specialty", "eq", "cardiology") not in intent_filter_set:
            add("REQUEST_FILTER_MISMATCH", "The requested specialty filter must be preserved")
        today = datetime.now(timezone.utc).date()
        date_range: tuple[date, date] | None = None
        if re.search(r"\btoday\b", lowered_request):
            date_range = today, today + timedelta(days=1)
        elif re.search(r"\btomorrow\b", lowered_request):
            date_range = today + timedelta(days=1), today + timedelta(days=2)
        elif re.search(r"\bthis week\b", lowered_request):
            week_start = today - timedelta(days=today.weekday())
            date_range = week_start, week_start + timedelta(days=7)
        elif re.search(r"\bthis month\b", lowered_request):
            month_start = today.replace(day=1)
            next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            date_range = month_start, next_month
        if date_range:
            start, end = date_range
            if not {("scheduled_at", "gte", start.isoformat()), ("scheduled_at", "lt", end.isoformat())}.issubset(intent_filter_set):
                add("REQUEST_FILTER_MISMATCH", "The requested date window must be preserved exactly")
        explicit_dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", request_text))
        if explicit_dates and not all(("scheduled_at", "gte", value) in intent_filter_set for value in explicit_dates):
            add("REQUEST_FILTER_MISMATCH", "Explicit calendar dates must be preserved in the appointment filters")
        # Explicit result bounds in the request must survive both model stages.
        limit = _limit(tree)
        if limit is None or limit > MAX_ROWS or limit != intent.limit:
            add("LIMIT_MISMATCH", "A bounded LIMIT matching the intent and at most 100 is required")
        requested_limit = re.search(r"\b(?:first|last|top|limit(?:\s+of)?)\s+(\d+)\b", request_text, re.I)
        if requested_limit and int(requested_limit.group(1)) != intent.limit:
            add("LIMIT_MISMATCH", "Explicit requested limit differs from structured intent")
        if intent.scope == "own_records" and intent.action != GENERAL_QUERY_ACTIONS["own"]:
            add("SCOPE_MISMATCH", "Own-record scope must use the actor-scoped action")
        if intent.scope == "aggregate" and (intent.action != GENERAL_QUERY_ACTIONS["aggregate"] or not aggregate_names):
            add("SCOPE_MISMATCH", "Aggregate scope requires an approved aggregate request")
        if intent.scope not in {"own_records", "aggregate"}:
            add("SCOPE_MISMATCH", "Unknown or broad scope cannot execute")

        actual_order: set[tuple[str, str]] = set()
        order = tree.args.get("order")
        if order:
            for item in order.expressions:
                if isinstance(item, exp.Ordered) and isinstance(item.this, exp.Column):
                    actual_order.add((_col_key(item.this), "desc" if item.args.get("desc") else "asc"))
                else:
                    add("ORDERING_MISMATCH", "Only ordering by an allowlisted column is supported")
        expected_order = {(item.column.rsplit(".", 1)[-1].casefold(), item.direction) for item in intent.ordering}
        actual_order = {(column.rsplit(".", 1)[-1], direction) for column, direction in actual_order}
        if actual_order != expected_order:
            add("ORDERING_MISMATCH", "SQL ordering differs from structured intent")
        canonical = tree.sql(dialect="postgres", pretty=False)
        analysis = SQLAnalysis(operation="read", tables=sorted(tables), columns=sorted(all_columns),
            projected_columns=sorted(actual_projection),
            filters=[IntentFilter(column=c, operator=op, value=value) for c, op, value in actual_filter_values],
            aggregations=sorted(aggregate_names), group_by=sorted(actual_grouping),
            ordering=[{"column": c, "direction": d} for c, d in sorted(actual_order)], limit=limit,
            canonical_fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(), canonical_sql=canonical,
            ast_node_count=node_count, ast_max_depth=max_depth, join_count=len(tree.args.get("joins") or []))
        return ValidationResult(valid=not reasons, reasons=reasons, analysis=analysis)


def _requests_mutation(text: str) -> bool:
    lowered = text.casefold()
    return bool(
        re.search(r"\b(delete|remove|erase|update|modify|change|insert|create|alter|drop|truncate|grant|revoke|cancel)\b", lowered)
        or re.search(r"\b(book|schedule|reschedule)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:appointment|visit)\b", lowered)
    )


def _tree_complexity(tree: exp.Expression) -> tuple[int, int]:
    stack = [(tree, 1)]
    count = 0
    depth = 0
    while stack:
        node, current_depth = stack.pop()
        count += 1
        depth = max(depth, current_depth)
        stack.extend((child, current_depth + 1) for child in node.iter_expressions())
    return count, depth


def _safe_count_expression(node: exp.Count) -> bool:
    if isinstance(node.this, exp.Star):
        return not bool(node.args.get("distinct"))
    if isinstance(node.this, exp.Distinct):
        expressions = node.this.expressions
        return len(expressions) == 1 and isinstance(expressions[0], exp.Column) and expressions[0].name.casefold() == "patient_id"
    return False

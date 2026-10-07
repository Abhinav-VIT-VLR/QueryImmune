-- Derive confidence and row-size bounds from eligible audit events. This prevents
-- a compromised runtime caller from inflating a learned result-size allowance.
SET ROLE qi_owner;
-- Remove any row-size statistic accepted by the earlier procedure unless it is
-- supported by the actual eligible audit history. This is a one-time repair.
UPDATE security.behavior_profiles p
SET feature_summary = p.feature_summary - 'result_size_max', updated_at = now()
WHERE p.feature_summary ? 'result_size_max'
  AND CASE
      WHEN jsonb_typeof(p.feature_summary->'result_size_max') = 'number'
       AND p.feature_summary->>'result_size_max' ~ '^[0-9]{1,3}$'
      THEN (p.feature_summary->>'result_size_max')::integer > least(100, coalesce((
          SELECT max(q.row_count)
          FROM security.query_logs q JOIN security.risk_events r USING (request_id)
          WHERE q.actor_id = p.actor_id AND q.decision = 'allow'
            AND q.execution_status = 'succeeded' AND q.created_at >= now() - interval '90 days'
            AND r.risk_score < 25 AND r.feature_snapshot->>'risk_level' = 'LOW'
            AND coalesce(r.feature_snapshot->'risk_reasons', '[]'::jsonb) <@ '["BASELINE_WARMUP","LEGITIMATE_WORKLOAD_DRIFT"]'::jsonb
      ), 0))
      ELSE true
  END;
DO $$
DECLARE
    v_function text;
    v_old_decl text := '    v_previous jsonb;';
    v_new_decl text := $decl$    v_previous jsonb;
    v_observed_max integer;$decl$;
    v_old_window text := 'OR p_window_start IS NULL OR p_window_end IS NULL OR p_window_end < p_window_start';
    v_new_window text := $window$OR p_window_start IS NULL OR p_window_end IS NULL OR p_window_end < p_window_start
       OR p_window_start < now() - interval '90 days' - interval '1 minute'
       OR p_window_end > now() + interval '1 minute'$window$;
    v_old_update text := '    UPDATE security.behavior_profiles SET update_status = ''superseded'', updated_at = now()';
    v_new_update text := $replacement$    SELECT coalesce(max(q.row_count), 0) INTO v_observed_max
    FROM security.query_logs q JOIN security.risk_events r USING (request_id)
    WHERE q.actor_id = p_actor_id AND q.decision = 'allow'
      AND q.execution_status = 'succeeded' AND q.created_at >= now() - interval '90 days'
      AND r.risk_score < 25 AND r.feature_snapshot->>'risk_level' = 'LOW'
      AND coalesce(r.feature_snapshot->'risk_reasons', '[]'::jsonb) <@ '["BASELINE_WARMUP","LEGITIMATE_WORKLOAD_DRIFT"]'::jsonb;
    IF p_confidence IS DISTINCT FROM round(least(0.95, v_eligible::numeric / 30), 2)
       OR (p_feature_summary ? 'result_size_max' AND (
           jsonb_typeof(p_feature_summary->'result_size_max') <> 'number'
           OR (p_feature_summary->>'result_size_max') !~ '^[0-9]{1,3}$'
           OR (p_feature_summary->>'result_size_max')::integer > 100
           OR (p_feature_summary->>'result_size_max')::integer > greatest(v_observed_max, coalesce((v_previous->>'result_size_max')::integer, 0))
       )) THEN
        RAISE EXCEPTION 'behavior profile statistics exceed observed bounds' USING ERRCODE = '23514';
    END IF;

    UPDATE security.behavior_profiles SET update_status = 'superseded', updated_at = now()$replacement$;
BEGIN
    SELECT pg_get_functiondef(
        'security.append_behavior_profile(uuid,integer,integer,numeric,timestamp with time zone,timestamp with time zone,jsonb)'::regprocedure
    ) INTO v_function;
    IF position(v_old_decl IN v_function) = 0 OR position(v_old_window IN v_function) = 0 OR position(v_old_update IN v_function) = 0 THEN
        RAISE EXCEPTION 'unexpected behavior profile function definition';
    END IF;
    v_function := replace(v_function, v_old_decl, v_new_decl);
    v_function := replace(v_function, v_old_window, v_new_window);
    v_function := replace(v_function, v_old_update, v_new_update);
    EXECUTE v_function;
END;
$$;
RESET ROLE;

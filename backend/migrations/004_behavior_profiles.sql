-- Append versioned behavioral profiles only from successfully executed, low-risk events.
SET ROLE qi_owner;

CREATE FUNCTION security.append_behavior_profile(
    p_actor_id uuid,
    p_expected_version integer,
    p_sample_count integer,
    p_confidence numeric,
    p_window_start timestamptz,
    p_window_end timestamptz,
    p_feature_summary jsonb
) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, healthcare, security
AS $$
DECLARE
    v_latest integer;
    v_eligible integer;
    v_next integer;
    v_allowed jsonb;
    v_previous jsonb;
BEGIN
    IF p_expected_version < 0 OR p_sample_count < 10 OR p_sample_count % 5 <> 0
       OR p_confidence < 0 OR p_confidence > 1
       OR p_window_start IS NULL OR p_window_end IS NULL OR p_window_end < p_window_start
       OR jsonb_typeof(p_feature_summary) <> 'object'
       OR (p_feature_summary - ARRAY['actions','tables','columns','operations','scopes','sensitivities','roles','signatures','query_structure','active_hours','frequency_sample_count','result_size_max']) <> '{}'::jsonb
       OR octet_length(p_feature_summary::text) > 16384 THEN
        RAISE EXCEPTION 'invalid behavior profile update' USING ERRCODE = '22023';
    END IF;

    SELECT coalesce(max(profile_version), 0) INTO v_latest
    FROM security.behavior_profiles WHERE actor_id = p_actor_id;
    IF v_latest <> p_expected_version THEN
        RAISE EXCEPTION 'stale behavior profile version' USING ERRCODE = '40001';
    END IF;

    SELECT count(*) INTO v_eligible
    FROM security.query_logs q
    JOIN security.risk_events r USING (request_id)
    WHERE q.actor_id = p_actor_id AND q.decision = 'allow'
      AND q.execution_status = 'succeeded' AND q.created_at >= now() - interval '90 days'
      AND r.risk_score < 25 AND r.feature_snapshot->>'risk_level' = 'LOW'
      AND coalesce(r.feature_snapshot->'risk_reasons', '[]'::jsonb) <@ '["BASELINE_WARMUP","LEGITIMATE_WORKLOAD_DRIFT"]'::jsonb;
    IF v_eligible <> p_sample_count OR v_eligible < 10 OR v_eligible % 5 <> 0 THEN
        RAISE EXCEPTION 'behavior profile sample gate rejected update' USING ERRCODE = '23514';
    END IF;

    -- The runtime may only learn categories already observed in eligible low-risk events,
    -- and each version must retain the prior profile's learned categories.
    WITH eligible AS (
        SELECT r.feature_snapshot, q.created_at
        FROM security.query_logs q JOIN security.risk_events r USING (request_id)
        WHERE q.actor_id = p_actor_id AND q.decision = 'allow'
          AND q.execution_status = 'succeeded' AND q.created_at >= now() - interval '90 days'
          AND r.risk_score < 25 AND r.feature_snapshot->>'risk_level' = 'LOW'
          AND coalesce(r.feature_snapshot->'risk_reasons', '[]'::jsonb) <@ '["BASELINE_WARMUP","LEGITIMATE_WORKLOAD_DRIFT"]'::jsonb
    )
    SELECT jsonb_build_object(
        'actions', coalesce((SELECT jsonb_agg(DISTINCT to_jsonb(feature_snapshot->>'action')) FROM eligible WHERE feature_snapshot ? 'action'), '[]'::jsonb),
        'tables', coalesce((SELECT jsonb_agg(DISTINCT to_jsonb(t.value)) FROM eligible, LATERAL jsonb_array_elements_text(coalesce(feature_snapshot->'tables','[]'::jsonb)) AS t(value)), '[]'::jsonb),
        'columns', coalesce((SELECT jsonb_agg(DISTINCT to_jsonb(c.value)) FROM eligible, LATERAL jsonb_array_elements_text(coalesce(feature_snapshot->'columns','[]'::jsonb)) AS c(value)), '[]'::jsonb),
        'operations', coalesce((SELECT jsonb_agg(DISTINCT to_jsonb(feature_snapshot->>'operation')) FROM eligible WHERE feature_snapshot ? 'operation'), '[]'::jsonb),
        'scopes', coalesce((SELECT jsonb_agg(DISTINCT to_jsonb(feature_snapshot->>'scope')) FROM eligible WHERE feature_snapshot ? 'scope'), '[]'::jsonb),
        'sensitivities', coalesce((SELECT jsonb_agg(DISTINCT to_jsonb(feature_snapshot->>'sensitivity')) FROM eligible WHERE feature_snapshot ? 'sensitivity'), '[]'::jsonb),
        'signatures', coalesce((SELECT jsonb_agg(DISTINCT to_jsonb(feature_snapshot->>'semantic_signature')) FROM eligible WHERE feature_snapshot ? 'semantic_signature'), '[]'::jsonb),
        'query_structure', coalesce((SELECT jsonb_agg(DISTINCT to_jsonb(feature_snapshot->>'semantic_signature')) FROM eligible WHERE feature_snapshot ? 'semantic_signature'), '[]'::jsonb),
        'roles', coalesce((SELECT jsonb_agg(DISTINCT to_jsonb(role_name)) FROM eligible, LATERAL jsonb_array_elements_text(coalesce(feature_snapshot->'role_context','[]'::jsonb)) AS r(role_name)), '[]'::jsonb),
        'active_hours', coalesce((SELECT jsonb_agg(DISTINCT to_jsonb(extract(hour from created_at)::integer::text)) FROM eligible), '[]'::jsonb)
    ) INTO v_allowed;
    SELECT feature_summary INTO v_previous FROM security.behavior_profiles
    WHERE actor_id = p_actor_id AND profile_version = v_latest;
    SELECT jsonb_object_agg(a.key, a.value || coalesce(v_previous->a.key, '[]'::jsonb))
    INTO v_allowed FROM jsonb_each(v_allowed) a;
    IF EXISTS (SELECT 1 FROM jsonb_each(v_allowed) a
               WHERE NOT (coalesce(p_feature_summary->a.key, '[]'::jsonb) <@ a.value)) THEN
        RAISE EXCEPTION 'profile contains unobserved behavior' USING ERRCODE = '23514';
    END IF;
    IF v_previous IS NOT NULL AND EXISTS (
        SELECT 1 FROM jsonb_each(v_previous) old
        WHERE jsonb_typeof(old.value) = 'array'
          AND NOT (coalesce(p_feature_summary->old.key, '[]'::jsonb) @> old.value)
    ) THEN
        RAISE EXCEPTION 'profile update removed established behavior' USING ERRCODE = '23514';
    END IF;
    IF (p_feature_summary->>'frequency_sample_count')::integer IS DISTINCT FROM v_eligible THEN
        RAISE EXCEPTION 'profile frequency count does not match approved events' USING ERRCODE = '23514';
    END IF;

    UPDATE security.behavior_profiles SET update_status = 'superseded', updated_at = now()
    WHERE actor_id = p_actor_id AND profile_version = v_latest AND update_status = 'active';
    v_next := v_latest + 1;
    INSERT INTO security.behavior_profiles(
        actor_id, profile_version, window_start, window_end, approved_sample_count,
        confidence, feature_summary, update_status
    ) VALUES (
        p_actor_id, v_next, p_window_start, p_window_end, v_eligible,
        p_confidence, p_feature_summary, 'active'
    );
    RETURN v_next;
END;
$$;

REVOKE ALL ON FUNCTION security.append_behavior_profile(uuid, integer, integer, numeric, timestamptz, timestamptz, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION security.append_behavior_profile(uuid, integer, integer, numeric, timestamptz, timestamptz, jsonb) TO qi_runtime;
RESET ROLE;

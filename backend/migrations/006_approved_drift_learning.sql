-- Allow low-risk, sustained workload drift to enter the same tightly gated learning path.
-- Supports databases that already applied the original Phase 4 migration as well as fresh setups.
SET ROLE qi_owner;
DO $$
DECLARE
    v_function text;
    v_old text := '["BASELINE_WARMUP"]';
    v_new text := '["BASELINE_WARMUP","LEGITIMATE_WORKLOAD_DRIFT"]';
    v_old_profile text := $old$    SELECT feature_summary INTO v_previous FROM security.behavior_profiles
    WHERE actor_id = p_actor_id AND profile_version = v_latest;
    IF EXISTS (SELECT 1 FROM jsonb_each(v_allowed) a$old$;
    v_new_profile text := $new$    SELECT feature_summary INTO v_previous FROM security.behavior_profiles
    WHERE actor_id = p_actor_id AND profile_version = v_latest;
    SELECT jsonb_object_agg(a.key, a.value || coalesce(v_previous->a.key, '[]'::jsonb))
    INTO v_allowed FROM jsonb_each(v_allowed) a;
    IF EXISTS (SELECT 1 FROM jsonb_each(v_allowed) a$new$;
BEGIN
    SELECT pg_get_functiondef(
        'security.append_behavior_profile(uuid,integer,integer,numeric,timestamp with time zone,timestamp with time zone,jsonb)'::regprocedure
    ) INTO v_function;
    IF position(v_old IN v_function) > 0 THEN
        v_function := replace(v_function, v_old, v_new);
    ELSIF position(v_new IN v_function) = 0 THEN
        RAISE EXCEPTION 'unexpected behavior profile function definition';
    END IF;
    IF position(v_old_profile IN v_function) > 0 THEN
        v_function := replace(v_function, v_old_profile, v_new_profile);
    ELSIF position(v_new_profile IN v_function) = 0 THEN
        RAISE EXCEPTION 'unexpected profile category validation';
    END IF;
    EXECUTE v_function;
END;
$$;
RESET ROLE;

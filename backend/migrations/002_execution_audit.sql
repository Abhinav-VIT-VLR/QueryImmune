-- Complete the execution lifecycle without granting UPDATE on the audit table.
SET ROLE qi_owner;

CREATE FUNCTION security.finish_query_log(
    p_request_id uuid, p_execution_status text, p_duration_ms integer, p_row_count integer
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, healthcare, security
AS $$
BEGIN
    IF p_execution_status NOT IN ('succeeded', 'failed')
       OR p_duration_ms < 0
       OR (p_row_count IS NOT NULL AND p_row_count < 0) THEN
        RAISE EXCEPTION 'invalid execution completion';
    END IF;
    UPDATE security.query_logs
    SET execution_status = p_execution_status,
        duration_ms = p_duration_ms,
        row_count = p_row_count
    WHERE request_id = p_request_id
      AND decision = 'allow'
      AND execution_status = 'not_run';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'query log is missing or already finalized'
            USING ERRCODE = '23503';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION security.finish_query_log(uuid, text, integer, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION security.finish_query_log(uuid, text, integer, integer) TO qi_runtime;

RESET ROLE;

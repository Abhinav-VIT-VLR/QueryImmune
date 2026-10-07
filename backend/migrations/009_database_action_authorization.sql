-- Authorization rules are data owned by PostgreSQL, keyed to the existing role catalog.
SET ROLE qi_owner;

-- Keep role names extensible. New roles without policy rows remain denied by default.
ALTER TABLE healthcare.roles DROP CONSTRAINT roles_name_check;
ALTER TABLE healthcare.roles
    ADD CONSTRAINT roles_name_format_check
    CHECK (name ~ '^[a-z][a-z0-9_]{0,62}$');

CREATE TABLE security.action_role_policies (
    action text NOT NULL CHECK (action ~ '^[a-z][a-z0-9_.]{0,127}$'),
    resource text NOT NULL CHECK (resource ~ '^[a-z][a-z0-9_.]{0,127}$'),
    role_id uuid NOT NULL REFERENCES healthcare.roles(id) ON DELETE RESTRICT,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (action, resource, role_id)
);
CREATE INDEX action_role_policies_role_idx
    ON security.action_role_policies(role_id, action, resource) WHERE enabled;

REVOKE ALL ON security.action_role_policies FROM PUBLIC;
GRANT SELECT ON security.action_role_policies TO qi_runtime;

RESET ROLE;

-- Read-only inventory for the selected project. Returns metadata only.
BEGIN READ ONLY;

SELECT current_database() AS database_name,
       current_setting('server_version') AS server_version;

SELECT namespace.nspname AS schema_name,
       relation.relname AS relation_name,
       CASE relation.relkind
           WHEN 'r' THEN 'table'
           WHEN 'p' THEN 'partitioned table'
           WHEN 'v' THEN 'view'
           WHEN 'm' THEN 'materialized view'
           WHEN 'f' THEN 'foreign table'
       END AS relation_kind,
       relation.relrowsecurity AS row_security_enabled
FROM pg_catalog.pg_namespace AS namespace
JOIN pg_catalog.pg_class AS relation ON relation.relnamespace = namespace.oid
WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
  AND namespace.nspname NOT LIKE 'pg_toast%'
  AND namespace.nspname NOT LIKE 'pg_temp_%'
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY schema_name, relation_name;

SELECT EXISTS (
    SELECT FROM pg_catalog.pg_namespace WHERE nspname = 'rover_backend'
) AS rover_backend_schema_exists;

COMMIT;

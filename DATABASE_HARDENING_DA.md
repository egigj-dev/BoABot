# DATABASE HARDENING — Step DA (report only; NOT applied)

The human rotates the DB password and decides repo privacy separately. These are
the parts that cap the damage of the NEXT leak. Nothing here was applied or
committed; the SQL/config is for the human to review and execute.

## Current exposed role (verified earlier: CK-3)

```
rolname        rolsuper  rolcreaterole  rolcreatedb  rolcanlogin
boa            true      true           true         true
owns: postgres, template1, boa
```

## Minimum grant set the code actually requires (derived from the tree)

Runtime (core/retrieve.py — the only core module that touches PG):
- `SELECT` on `chunks` (dense cosine + lexical `to_tsquery` + fetch_chunks_by_ids).
- Nothing else: core/api, core/trust, core/callcenter never open a DB connection.

Maintenance (scripts/, run manually, not by the serve path):
- `rebuild_chunks.py`: `CREATE TABLE chunks_backup`, `DELETE FROM chunks`,
  `INSERT INTO chunks (...)».
- `backfill_visibility.py`: `ALTER TABLE chunks ADD COLUMN IF NOT EXISTS`,
  `UPDATE chunks SET ...`.
- `backfill_doc_scope.py`, `probe_chunk_policy.py`, `rebuild_chunks_dedup.py`:
  SELECT (+ their own INSERT for dedup re-insert).

No runtime path needs CREATE DATABASE, CREATEROLE, SUPERUSER, or ownership of
postgres/template1. The maintenance scripts need DDL on the `chunks` table only.

## Proposed SQL (for the human)

```sql
-- 1. Create the least-privilege application role.
CREATE ROLE boa_app LOGIN PASSWORD '<new-strong-password>';

-- 2. Runtime: read-only on the chunks table.
GRANT CONNECT ON DATABASE boa TO boa_app;
GRANT USAGE ON SCHEMA public TO boa_app;
GRANT SELECT ON chunks TO boa_app;
-- (if the app ever needs the sequence/other tables, extend here — it does not today)

-- 3. Maintenance (optional separate role boa_maint, or grant narrowly):
GRANT SELECT, INSERT, UPDATE, DELETE ON chunks TO boa_app;
GRANT CREATE ON SCHEMA public TO boa_app;          -- for chunks_backup
-- maintenance-only, not needed by the serving path.

-- 4. Revoke the over-privileged defaults from the existing published role:
ALTER ROLE boa NOSUPERUSER NOCREATEDB NOCREATEROLE;
-- (do AFTER the human rotates its password; consider dropping it once boa_app works)
```

## Required BOABOT_DSN change

```
before: postgresql://boa:boa@127.0.0.1:5433/boa
after : postgresql://boa_app:<new-strong-password>@127.0.0.1:5433/boa
(The value goes in the deployment's .env / environment, never in the repo.)
```

## Configuration changes (postgresql.conf, human applies + restarts PG)

```ini
listen_addresses = '127.0.0.1'   # was '*'; the localhost bind was incidental
log_connections = on              # was off
log_disconnections = on           # was off
```

`log_connections/disconnections = on` gives detection for the next attempt;
`listen_addresses='127.0.0.1'` makes the localhost-only posture intentional.
Both are cheap and do not affect the app (same host connection).

## Notes
- Do NOT apply during the handoff; the human applies, then restarts Postgres,
  then switches BOABOT_DSN, then rotates the original `boa` password.
- After the switch, `boa` (superuser) should be disabled/dropped per the
  human's risk tolerance; keep it only if a break-glass superuser is required.
- The maintenance scripts that ALTER/DELETE must run as a role with those
  grants (boa_app with the maintenance grant, or a dedicated boa_maint).
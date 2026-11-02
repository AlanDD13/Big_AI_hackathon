# Shared contracts

Status: kickoff template, not an agreed architecture. Owner: TBD.

Before parallel implementation, agree and replace these fields:

- Demo: one user action, expected result, and definition of working.
- UI -> API: method/path, request, response, error format, and one JSON example.
- API -> AI/data: input, output, model/provider, timeout, and failure behavior.
- Data: shared types, units, IDs, storage/schema owner, and mock fixtures.
- Environment: variable names and which component reads each (never secret values).
- Local run: component commands, ports, and one end-to-end verification command.
- Delivery: lead, merge order, deployment target, and demo fallback.

Keep durable decisions here and link them from the board. A comment thread is
not the sole source for the current API contract.

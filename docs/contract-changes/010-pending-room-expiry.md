# 010 PENDING_ENTROPY room count expires abandoned jobs

- **Requested by:** James, 2026-10-15 ("deal with abandoned jobs")
- **Status:** accepted 2026-10-15
- **Affects:** WS0 (`qmc_core.ddb.job_statuses`, HANDOFF §4.2 table), WS2 (`submit._pending_room`), WS5 (README known limit, 008 note)

## Need

008's accepted limit: a PENDING_ENTROPY job whose seeder event was lost stays PENDING past `pending_until` and counts toward `MAX_PENDING_ENTROPY` until a GET expires it or it ages out of the newest 50 JOBLIST entries. Ten such jobs block every new opt-in submit.

## Change

- `job_statuses` projects `job_id, #s, #pu` (`#pu` = `pending_until`). Non-PENDING items are unchanged (`pending_until` is absent on them); `GET /jobs` ignores the extra attribute.
- `_pending_room` (WS2): a PENDING job with `now > pending_until` is not counted; it is ended with `pending.fail_pending(table, id, "PENDING_EXPIRED")`, the same conditional `pending_fail` GET's backstop runs. A refusal (the seeder won the race) is already a no-op.

## Alternatives considered

- A scheduled reaper: a new Lambda, rule and role for one rare case; more resources against I7.
- Reading each PENDING job's META: up to 50 GetItems per submit instead of one BatchGetItem.

## Impact on invariants

I1 unchanged: expiry only moves PENDING → FAILED through the existing conditional builder. No result bits change; no new attribute, metric or resource.

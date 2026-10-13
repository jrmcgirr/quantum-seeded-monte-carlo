# 003 Qrypt 403 is QRYPT_FORBIDDEN, not QRYPT_QUOTA_EXHAUSTED

- **Requested by:** WS4 (fail-closed drill finding), 2026-10-13 11:52 CT
- **Status:** done (MR !9, merged to main 2026-10-13)
- **Affects:** WS0 (`qmc_core.errors`, `qmc_core.seeds`, `tests/core`, HANDOFF §5.3, §11, R2, WS2 prompt), WS2 (`src/qmc_api/qrypt.py` FakeEntropySource map, `tests/api`), WS4 (deployed fail-closed test, TEST_RESULTS), WS5 (docs quoting the code)

## Need

The fail-closed drill (decoy token `/qmc/qrypt-api-key-invalid`, build d0af0b1, 11:51Z) got HTTP **403** from Qrypt, not 401, so the API answered `QRYPT_QUOTA_EXHAUSTED`: a wrong cause for a bad token. Probe from WS0 (one request, fake token, no quota):

```
Authorization: Bearer NOT-A-REAL-TOKEN  →  HTTP 403  {"error":"Forbidden or entropy quota reached"}
no Authorization header                 →  HTTP 401
```

Qrypt sends the same status and body for a rejected token and an exhausted quota, so no client can tell them apart.

## Change

- `ErrorCode.QRYPT_QUOTA_EXHAUSTED` is renamed `ErrorCode.QRYPT_FORBIDDEN` (still 503), message "Qrypt refused the request (403): the token is invalid or expired, or the entropy quota is used up."
- `seeds.parse_qrypt_response`: 403 → `QRYPT_FORBIDDEN`. 401 stays `QRYPT_AUTH_FAILED` (in practice: no or malformed header).
- The deployed fail-closed drill asserts 503 `QRYPT_FORBIDDEN`.

## Alternatives considered

- Keep the code name, change the message: fewer edits, but the code shown to graders and in `SeedingFailures` `cause` stays misleading.
- Map 403 to `QRYPT_AUTH_FAILED`: a real quota exhaustion would be reported as a bad token.
- Parse the 403 body: the body is the same for both causes.

## Impact on invariants

None. I1 (fail closed, nothing written, cause named) holds; the named cause is now accurate.

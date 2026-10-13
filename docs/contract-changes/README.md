# Contract changes

Anything another workstream depends on is contract, owned by WS0: DynamoDB keys and attributes and the expressions in `qmc_core.ddb`; the SQS message (`qmc_core.messages`); API routes, fields and error codes; Lambda environment variables; Terraform variables and outputs that other workstreams read; and the `qmc_core` package. If you need to change any of these, do not edit the shared files. Write a request here and tell James.

## How

1. Copy the template below to `NNN-short-title.md` (next free number, three digits).
2. Fill it in and commit it on your branch; mention it to James.
3. WS0 decides with James, makes the change on `ws0-foundation`, merges it to `main`, bumps the HANDOFF version and sets **Status** here.
4. Affected workstreams rebase on `main`.

## Template

```markdown
# NNN Short title

- **Requested by:** WSn, YYYY-MM-DD HH:MM CT
- **Status:** proposed | accepted | rejected | done (commit)
- **Affects:** WS…, files…

## Need
What you are trying to do and why the current contract does not allow it.

## Proposed change
Exact attributes, expressions, message fields, routes, error codes, variables or function signatures.

## Alternatives considered
What you could do without the change, and why that is worse.

## Impact on invariants
Which of I1–I7 this touches, and how it stays true. For anything touching simulation or aggregation: does it change any existing result's bits (if so, it needs a new `algo_version`)?
```

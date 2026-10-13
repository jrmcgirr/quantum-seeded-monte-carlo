# api

The API Lambda `qmc-<env>-api` with its 14-day log group and least-privilege role, the Function URL (`AWS_IAM`, `BUFFERED`), the invoke-only client role `qmc-<env>-client`, and optionally the grader user `qmc-<env>-grader` (policy through the `qmc-<env>-graders` group; access key created by CLI, never in state). The Qrypt token parameter is referenced by ARN string only. HANDOFF §5.1, §8.2–8.4, D26.

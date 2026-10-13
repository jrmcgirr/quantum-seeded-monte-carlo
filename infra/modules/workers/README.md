# workers

The worker Lambda `qmc-<env>-worker` (python3.13, x86_64, runtime pinned on function update, `QMC_ENFORCE_PINNED_DISPATCH=true`), its 14-day log group, a least-privilege role, and the stack's only SQS event source mapping (batch 1, `ReportBatchItemFailures`, `maximum_concurrency`). HANDOFF §7.9, §8.2–8.4.

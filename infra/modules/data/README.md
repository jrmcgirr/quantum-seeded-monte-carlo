# data

The DynamoDB table `qmc-<env>` (PROVISIONED, `pk`/`sk`, TTL on `ttl`, PITR off, AWS-owned key), the shard queue `qmc-<env>-shards` and its DLQ (both SSE-SQS), the redrive policy, and a TLS-only policy on each queue. HANDOFF §6.1, §8.2.

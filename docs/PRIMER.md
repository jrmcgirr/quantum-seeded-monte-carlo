# Quantum-Seeded Monte Carlo — Research Primer

> The deep-research report from the planning session of 2026-10-12 (five research sub-agents and a report writer; notes on file with the planning session). Also saved to the claude.ai project "Qrypt" as `research/deep-research-report.md`. Decisions D1–D21 below are the research recommendations; the final choices are in `docs/DECISIONS.md` and `docs/HANDOFF.md` §3 (they differ for D16, D19 and D21).

## Seed, Fan Out, and Replay Monte Carlo for $0

The platform is buildable in two days at $0 a month, but two things decide whether it passes, and neither is the Monte Carlo maths. The first is **bit-identical replay**. NumPy 2.x sends `np.exp` to an AVX-512 kernel on CPUs that have it, and AWS Lambda x86_64 runs on a mix of Intel and AMD hosts. The same seed can therefore produce different shard sums on different invocations unless the SIMD dispatch is pinned (or arm64 is chosen) and recorded with every job. The second is **Qrypt**: one rate-limited (30 requests per 10 s per token), quota-metered, unmeasured HTTPS call on the synchronous submit path. Its latency against the 800 ms p95 target, and whether self-registration still works, are unknown until you try it. Everything else is routine serverless work done carefully. Use one SQS queue with one event source mapping, one DynamoDB table in provisioned mode, conditional writes for idempotency and fan-in, and a PARTIAL state set by the worker's final attempt plus a deadline check, while the DLQ is left untouched so its alarm fires. The free-tier arithmetic for 100 jobs a day of 1M paths comes to **$0.00**, and the binding constraint is not compute (about 13% of Lambda GB-s) but **SQS idle long-polling (about 72% of the 1M-request allowance)**. A second SQS event source mapping, for example for a PENDING_ENTROPY retry queue, pushes past 1M requests a month. The report ends with a risk list to clear in the first hours and a decision-by-decision walkthrough with recommended defaults.

## Pricing the option is trivial; reproducing it bit-for-bit is not

The target is a European call at S0 = K = 100, r = 0.05, σ = 0.2, T = 1. The research notes computed the closed form as **C = 10.450583572185565** (d1 = 0.35, d2 = 0.15) and the exact standard deviation of the discounted payoff as **14.7194**. That puts the **standard error at 1M paths at 0.014719** and the 3-SE acceptance band at **±0.0442**. These are first-hand computations in the research notes, verified numerically rather than cited from a textbook. Exact terminal sampling, `S_T = S0·exp((r − σ²/2)T + σ√T·Z)`, has no discretisation bias because the payoff depends only on S_T. Three 1M-path runs with different NumPy BitGenerators landed at z-scores of −1.37, −0.07 and −0.02. One subtlety matters for the test suite: with a fixed seed, the "within 3 SE" test is deterministic, so it always passes or always fails for that seed. About 0.27% of seeds fail by bad luck, so pick the committed test seed once and freeze it.

Compute is negligible. In the notes' local benchmark, one million paths (normals, exp, payoff, two sums) took **about 25 ms single-threaded** with PCG64DXSM, and peak memory was about 55 MB unchunked. End-to-end time is dominated by Qrypt, SQS fan-out, cold starts and DynamoDB writes, not by simulation. Lambda gives one vCPU-equivalent at 1,769 MB ([AWS Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html)), so even 512–1,024 MB workers finish a 100k-path shard in milliseconds once warm.

Seed derivation is solved by NumPy itself. `SeedSequence(master, spawn_key=(i,))` was verified to be **bit-identical to `SeedSequence(master).spawn(n)[i]`**, so each worker derives its own stream from (master seed, shard index) without coordination. NumPy's parallel-RNG guide puts the collision probability at about n²·2⁻¹²⁸ and guarantees unique states for one level of spawning. It also explicitly marks `root_seed + worker_id` as unsafe ([NumPy parallel RNG](https://numpy.org/doc/stable/reference/random/parallel.html)). Two traps sit here. `spawn()` is stateful: a second call continues from index n rather than restarting at 0, so replay code must never call it on a reused object. And a 256-bit master integer is hashed into a default 128-bit pool, so the "256-bit" claim applies to the stored seed, not to the RNG state. That is fine statistically, but write it down.

The hard problem is reproducibility across machines. NumPy's compatibility policy promises identical streams only for "the same build of numpy, in the same environment, on the same machine". It warns that different CPUs' floating point "can cause differences in certain edge cases". It also reserves the right to change Generator algorithms in X.Y releases, and it does not guarantee that `random(5)` equals five calls to `random()` ([NumPy compatibility policy](https://numpy.org/devdocs/reference/random/compatibility.html)). A local experiment in the notes (NumPy 2.5.3, Xeon with AVX-512) showed this is a live problem, not a theoretical one. The normals were bit-identical across dispatch modes. But `np.exp` on float64 dispatched to the AVX-512 (X86_V4) kernel, which matched glibc in only **955,363 of 1,000,000 elements (max 1 ULP off)**. With `NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR"` it matched glibc in all 1,000,000. The end-to-end hash of shard (sum, sum_sq) differed between the two modes. Meanwhile, a 2025 measurement study found **four x86_64 CPU types on Lambda (three Intel Xeon models and one AMD EPYC)**, with the mix changing over time in some zones ([Cordingly et al., UCC 2025](https://faculty.washington.edu/wlloyd/papers/Cordingly_CPU_Distribution_Manipulation_UCC2025-cr.pdf)). AWS documents arm64 as a single processor family, Graviton2, and describes x86_64 only as "x86-based processors" ([AWS Lambda architectures](https://docs.aws.amazon.com/lambda/latest/dg/foundation-arch.html)). Nobody has published which Lambda x86 hosts expose AVX-512. The safe assumption is that it varies per invocation.

Three more rules keep the arithmetic deterministic once the kernels are pinned. First, use a fixed chunk size inside each shard (for example 2¹⁷), and record it, because NumPy does not guarantee that chunked and unchunked draws agree. Second, keep BLAS out of the hot path: `a @ a` produced a different last bit from `(a*a).sum()` because OpenBLAS picks kernels by CPU. Third, aggregate shards with `math.fsum`, which is exactly rounded and therefore independent of order. The storage format matters too. boto3 refuses Python floats, and its Decimal context raises `Inexact` on `Decimal(0.1)` ([boto3 dynamodb types](https://docs.aws.amazon.com/boto3/latest/_modules/boto3/dynamodb/types.html)). Store `sum` and `sum_sq` as `float.hex()` strings so bit-exactness is explicit and replay comparison is a string compare. Under the specified {sum, sum_sq, n} contract, naive variance can cancel catastrophically when the standard deviation is small relative to the mean. A test with offset 1e9 returned a negative variance ([Wikipedia: variance algorithms](https://en.wikipedia.org/wiki/Algorithms_for_calculating_variance)). For this option the mean² is about 109 against a variance of about 217, so float64 is comfortable, but say so in the README.

The honest replay contract follows from all this. Replay is bit-identical for the same NumPy version, architecture, SIMD dispatch target, glibc and algorithm version. Every job record stores that fingerprint. Replay creates a new job (`replay_of = <id>`), reuses the stored master seed without calling Qrypt, compares the fingerprint, and reports `bit_identical` plus per-shard hex diffs. AWS's own Lambda Monte Carlo case study (Playtech) used the same root-seed-down-the-tree idea and stressed versioning every component ([AWS HPC Blog](https://aws.amazon.com/blogs/hpc/high-burst-cpu-compute-for-monte-carlo-simulations-on-aws/)).

## Qrypt is a single rate-limited, unmeasured dependency on the hot path

The documented API is small: `GET https://api-eus.qrypt.com/api/v1/quantum-entropy?size=N`, with `Authorization: Bearer <token>`, `size` counted in 1 KiB blocks (1–512), and a response of `{"random": [base64…], "size": N}` in standard padded base64 ([Qrypt EaaS docs](https://docs.qrypt.com/eaas/)). The documented errors are 400, 401 (token invalid or expired), **403 (allotment used up)**, **429 (more than 30 requests per 10 s per token)**, 500 and **503 (temporary entropy shortage)**. The docs say nothing about error bodies or a `Retry-After` header. The allotment is a **monthly byte quota that resets on the account's opening anniversary**, and raising it means emailing support ([Qrypt FAQs](https://docs.qrypt.com/faqs/)). Qrypt's own production C client hardcodes `api-eus` and treats 400 and 401 as fatal, 403 as a fixed 60 s backoff, and everything else as retryable with exponential backoff. It sets no timeouts ([rngd_qrypt.c](https://raw.githubusercontent.com/nhorman/rng-tools/master/rngd_qrypt.c)). That suits a daemon, not a latency-bound API.

Getting a token is the first unknown. Getting Started describes self-service signup: email, a 4-digit code, then a Tokens page where you scope the token to Entropy, Keygen or both, set an expiry, and see the token once ([Qrypt Getting Started](https://docs.qrypt.com/getting_started)). The current EaaS and rngd pages instead say tokens come from contacting Qrypt ([Qrypt EaaS docs](https://docs.qrypt.com/eaas/); [Qrypt rngd docs](https://docs.qrypt.com/eaas/rngd/)). No source publishes the free quota or the expiry options. Register on day 0. Scope the token to Entropy only, record its expiry, and keep an offline copy, because a lost token can only be recovered through support.

Latency is the second unknown, and it hits the 800 ms p95 submit budget directly. Qrypt publishes no latency or SLA figures ([Qrypt EaaS data sheet](https://www.qrypt.com/s/EaaS-Data-Sheet.pdf)), and the research sandbox could not reach the host, so the number has to be measured from the deployment region. The submit path stacks a DynamoDB limiter update (about 10 ms), the Qrypt call, a job `PutItem` and one `SendMessageBatch`. Cold starts are the bigger threat at this traffic level. A Python handler that does nothing initialises in about 93 ms, but one that makes a single secrets call during init took about 543 ms ([Chariot Solutions](https://chariotsolutions.com/blog/post/whats-the-point-of-lambda-snapstart/)). At 100 jobs a day, many submits will probably land on cold environments, which adds an SSM decrypt and a fresh TLS handshake to Qrypt (this is an inference, not a measurement). Keep the API function free of NumPy. Hold a module-level `HTTPSConnection` for warm reuse. Enforce one overall deadline using `context.get_remaining_time_in_millis()`, because a `urllib` timeout applies per socket operation, not to the whole call. Emit `QryptLatencyMs` from the first deployment.

Derive the master seed with **HKDF-SHA256 over the full 1 KiB block**. Extract uses a fixed, public, project-specific salt; Expand uses `info = "qmc/master-seed/v1|" + job_id` and returns L = 32 bytes. RFC 5869 allows a fixed non-secret salt and uses `info` for domain separation ([RFC 5869](https://www.rfc-editor.org/rfc/rfc5869)). Compressing 8,192 bits into 256 tolerates a large min-entropy shortfall, and that matters because Qrypt publishes no SP 800-90B assessment of its cloud output. Its own descriptions of the physical source also disagree between the data sheet and the FAQ ([Qrypt FAQs](https://docs.qrypt.com/faqs/); [Qrypt EaaS data sheet](https://www.qrypt.com/s/EaaS-Data-Sheet.pdf)). **Do not mix in `os.urandom`.** NIST SP 800-133r2 shows that combining independent sources can only add strength ([NIST SP 800-133r2](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-133r2.pdf)). Mixing still breaks the "seeded only from Qrypt" provenance claim, and it lets a silent Qrypt failure pass as success. Fail closed on any non-200 response, schema mismatch, decoded length other than 1,024 bytes, all-zero block, or repeat of a recently stored `raw_sha256`. Return 503 with a machine-readable code and write no job row. Store the provenance with the job: the raw-block SHA-256, KDF name and version, fetch time, latency, HTTP status, and the master seed in hex. Prove fail-closed with a unit test that patches `os.urandom`, `secrets` and `time` to raise on the failure path.

Workers and replays never call Qrypt, so only submit spends the rate limit. The limiter is a DynamoDB fixed-window counter: `ADD n :1` on item `RL#qrypt/<floor(now/10)>` with condition `n < cap`, plus a TTL. The cap matters. Any rolling 10-second span touches at most two aligned windows, so **a cap of 15 per window guarantees at most 30 in any rolling 10 s**. A cap of 25 or 30, which the Qrypt notes suggested, allows up to 50–60 across a window boundary. Retries count against the same budget. Reserved concurrency on submit caps concurrency, not rate: with C = 3 and 200 ms latency, Qrypt could still see about 150 requests per 10 s. On a new account you may not be able to reserve concurrency at all (see below). At 1 KiB per job, a 10 MB entropy budget covers about 10,000 submissions. The real ceiling is the account's unpublished monthly quota.

## One queue, one table, and conditional writes carry correctness

The worker path follows AWS guidance closely. Use a **standard** SQS queue (FIFO adds nothing, because processing is still at-least-once). Set batch size 1, batching window 0, and `ReportBatchItemFailures` on. Put the DLQ on the **source queue's redrive policy**, because Lambda on-failure destinations do not apply to SQS event source mappings ([Lambda: retaining failed records](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-retain-records.html)). Set the visibility timeout to at least 6× the function timeout, and note that AWS recommends `maxReceiveCount` of at least 5 ([Lambda: configure SQS ESM](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html)). Cap fan-out with the event source mapping's **`MaximumConcurrency`** (2–1,000), not with reserved concurrency alone. In AWS's demo, a function limited to 5 by reserved concurrency **sent 9 of 25 messages to its DLQ purely from throttling**, while the same limit set as maximum concurrency produced no DLQ messages, because Lambda stops polling at the cap ([AWS Compute Blog](https://aws.amazon.com/blogs/compute/introducing-maximum-concurrency-of-aws-lambda-functions-when-using-amazon-sqs-as-an-event-source/)). The mapping starts with 5 concurrent batches and adds up to 300 invocations per minute ([Lambda SQS scaling](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-scaling.html)), so 10 shards are all running within seconds and the 60-second target for 1M paths is easy.

Fan-in is where most homemade designs break. The classic "conditional shard put, then `ADD completed :1`" is **wrong under at-least-once delivery**. A crash between the two writes leaves the job stuck forever. Incrementing on every retry double-counts. Skipping the increment on `ConditionalCheckFailed` means the counter never recovers. The recommended pattern has three steps:

1. A conditional shard put (`attribute_not_exists`) on `pk=JOB#id, sk=SHARD#0007`.
2. `ADD done_shards :{idx}` into a **Number Set** on the job item, with `ReturnValues=UPDATED_NEW`. Adding an element that is already in the set changes nothing, so retries repair rather than corrupt.
3. Whichever worker sees |done_shards| = n_shards performs a **conditional status transition** (`status = RUNNING`) to finalise.

On `ConditionalCheckFailed` from the shard put, the worker must still run steps 2 and 3. Two workers can race to finalise. Because aggregation is deterministic, both compute identical bits, and the loser's conditional write simply fails. `TransactWriteItems` is also correct but costs 2 WCU per item, even when cancelled ([DynamoDB read/write operations](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/read-write-operations.html)). Step Functions is the most observable option but blows through its free tier (next section). As a safety net, `GET /jobs/{id}` can lazily aggregate any job whose shards are all present but which is still RUNNING.

PARTIAL needs its own mechanism, because SQS delays failure by design. After a function error, a message reappears only when its visibility timeout expires ([Lambda: SQS error handling](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-errorhandling.html)). Time to DLQ is therefore about maxReceiveCount × visibility timeout: 9 minutes at 3 × 180 s. The recommended design has two layers:

- **The worker marks its own final attempt.** When `ApproximateReceiveCount ≥ maxReceiveCount`, it runs `ADD failed_shards :{idx}` and reports the item failure ([SQS ReceiveMessage](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_ReceiveMessage.html)).
- **A deadline reaper evaluated lazily on GET** marks RUNNING jobs that are past `deadline_at` with missing shards as PARTIAL. This also catches crashes, out-of-memory errors and timeouts on the final attempt, where no `except` block ever runs.

Do **not** attach a consumer to the DLQ. An event source mapping deletes messages on success, so DLQ depth can return to zero within the same minute and the "forced failure trips the DLQ alarm" requirement fails. Alarm on `ApproximateNumberOfMessagesVisible > 0` on the DLQ, which AWS calls "the recommended metric for DLQs". Do not use `NumberOfMessagesSent`, which does not count messages moved by redrive ([SQS CloudWatch metrics](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-available-cloudwatch-metrics.html)).

A single DynamoDB table holds everything: job META items, SHARD items with zero-padded sort keys (so a strongly consistent Query returns them in index order), and rate-limit window items, with TTL on all of them. TTL deletes consume no WCU, but they happen "within a few days", so reads must filter expired items ([DynamoDB TTL](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html)). A 10-shard job costs about 22–25 WCU in total: submit, 10 shard puts, 10 set-ADDs, finalise and the limiter. DynamoDB banks up to 300 s of unused capacity as burst ([DynamoDB burst capacity](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/burst-adaptive-capacity.html)), so a small provisioned table absorbs bursts of hundreds of jobs. Configure boto3 with adaptive or standard retries so a throttle retries inside the invocation instead of becoming a slow SQS retry. Skip the Powertools Idempotency utility. It keys on SQS `messageId`, which is the wrong identity: a redriven or re-sent shard gets a new message ID. It also costs about 2 WCU per call ([Powertools Idempotency](https://docs.powertools.aws.dev/lambda/python/latest/utilities/idempotency/)). The natural key (job_id, shard_index) is already enforced by the conditional put at 1 WCU.

## The free tier is won or lost on SQS polling and CloudWatch counts

First, the account type. Since 15 July 2025, new AWS accounts choose a **Free plan**, which runs on credits and is suspended at 6 months with data erased 90 days later, or a **Paid plan** ([AWS News Blog](https://aws.amazon.com/blogs/aws/aws-free-tier-update-new-customers-can-get-started-and-explore-aws-with-up-to-200-in-credits); [AWS Free Tier FAQ](https://aws.amazon.com/free/free-tier-faqs/)). Always-free allowances apply on both. A "$0 per month forever" claim therefore needs a Paid-plan account (or a legacy one) that stays inside always-free limits. On the Free plan, overage quietly consumes credits, which hides a broken cost design. Building this project also earns two of the $20 activity credits: a Function URL web app and a Budget alert.

The allowances that bind are narrower than most people expect:

- **Lambda:** 1M requests and 400,000 GB-s, with init code billed as duration ([Lambda pricing](https://aws.amazon.com/lambda/pricing/)).
- **DynamoDB:** 25 WCU and 25 RCU, **provisioned mode only**, per region per payer account, shared by every table and index. On-demand is not covered, and PITR has no free allowance ([DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/provisioned/)).
- **SQS:** 1M requests, where every action counts, including empty receives ([SQS pricing](https://aws.amazon.com/sqs/pricing/)).
- **CloudWatch:** **10 custom metrics** (each unique dimension combination counts), **10 standard-resolution alarm metrics**, 3 dashboards, and 5 GB of logs. GetMetricData is always charged ([CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)).
- **KMS:** 20,000 requests, with **customer-managed keys at $1 per month each** ([KMS pricing](https://aws.amazon.com/kms/pricing/)).
- **Budgets:** monitoring is free ([Budgets pricing](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/)).
- **Step Functions:** only **4,000 free state transitions** a month ([Step Functions pricing](https://aws.amazon.com/step-functions/pricing/)).

The trap is idle polling. According to an AWS-attributed re:Post answer, an SQS event source mapping keeps 5 long-polling pollers, which comes to about **648,000 ReceiveMessage calls a month for an idle queue**, all billable ([re:Post](https://www.repost.aws/questions/QUw66hGkRwSWiBSjkIJhTgOw/how-to-calculate-cost-of-sqs-queue-when-consumed-by-a-batched-lambda)). Lambda can scale an idle mapping down to 2 pollers, but **"this optimization isn't available when maximum concurrency is set"** ([Lambda SQS scaling](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-scaling.html)). Since the design needs `MaximumConcurrency`, budget for the full 648k.

**Monthly arithmetic for 100 jobs/day × 1M paths (3,000 jobs; 10 shards × 100k paths each; ~10% replays; 10 status polls per job; /health every 5 min):**

| Service | Usage per month | Allowance | Share | Notes |
|---|---|---|---|---|
| Lambda requests | ≈ 75,000 (3,000 submit + 30,000 workers + 30,000 polls + 3,300 replay + 8,640 health) | 1,000,000 | 7.5% | |
| Lambda GB-s | ≈ 52,500 (workers 1 GB × 1.5 s × 33,000 ≈ 50,700; API ≈ 1,800) | 400,000 | ≈ 13% | Break-even ≈ 12 GB-s per shard; the 1.5 s estimate is conservative and should be measured |
| SQS requests | ≈ 720,000 worst case (648k idle polls + ≤ 33k receives + ≤ 33k deletes + 3,300 batched sends) | 1,000,000 | ≈ 72% | **Binding constraint.** A second mapped queue adds ≈ 648k, for ≈ 1.37M (≈ $0.15 over) |
| DynamoDB writes | ≈ 72,600 (3,300 × ~22) | 25 WCU × 2.59M s ≈ 64.8M | 0.1% | Peak 10 shards/s needs ≈ 20 writes/s; burst bucket covers it |
| DynamoDB reads | ≈ 40,000 | 25 RCU sustained | ≪ 1% | Eventually consistent polls at 0.5 RCU |
| DynamoDB storage | ≈ 33 MB/month (≈ 400 MB/yr without TTL) | 25 GB | < 2% | |
| CloudWatch custom metrics | 8 (no per-job dimensions) | 10 | 80% | Doubles per extra environment if namespaced per env |
| CloudWatch alarms | 8–9 standard (60 s) | 10 | 80–90% | Budget alarm lives in Budgets, not CloudWatch |
| CloudWatch Logs | ≈ 75 MB ingest (+≈ 40 MB if EMF), 14-day retention | 5 GB | ≈ 2% | |
| KMS | < 3,300 `aws/ssm` decrypts (once per API cold start) | 20,000 | < 17% | SSE-SQS and AWS-owned DynamoDB key need no KMS calls |
| SNS | dozens of emails | 1,000 emails, 1M publishes | ≈ 0% | |
| Data transfer out | ≈ 150 MB | 100 GB | ≈ 0% | |
| SSM Standard parameter | 1 SecureString | free | 0 | |
| **Step Functions (if used)** | ≈ 16,500 transitions | 4,000 | 412% | **≈ $0.31/month: breaks $0** |
| **Total** | | | | **$0.00** with one mapping, provisioned DynamoDB, no Step Functions, ≤10 metrics/alarms, no customer-managed keys, log retention set |

The sources behind the table: the request and GB-s figures come from the research notes' worked model, built on [Lambda pricing](https://aws.amazon.com/lambda/pricing/), [SQS pricing](https://aws.amazon.com/sqs/pricing/), [DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/provisioned/), [CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/), [SNS FAQ](https://aws.amazon.com/sns/faqs/) and [Step Functions pricing](https://aws.amazon.com/step-functions/pricing/). Worker duration and idle-poll counts are assumptions to confirm from REPORT log lines and the queue's `NumberOfEmptyReceives` metric.

Three conclusions follow. Compute is nowhere near the limit: at about 20 GB-s per job, Lambda alone could carry on the order of 20,000 jobs a month. The SQS mapping count, the DynamoDB billing mode and the CloudWatch metric/alarm counts are what can break $0, and each needs one explicit design rule. And several "secure by default" toggles cost money. A customer-managed KMS key costs $1 a month, as much as the whole Budget limit. PITR is billed per GB-month. Setting `server_side_encryption { enabled = true }` on DynamoDB without a key reportedly switches from the free AWS-owned key to the KMS-billed `aws/dynamodb` key; that comes from training knowledge and needs verifying.

## Terraform hygiene and IAM decide "clean destroy, no drift"

The QRYPT_API_KEY rule is about state, not output. The `aws_ssm_parameter` data source defaults `with_decryption = true`, and its docs warn that "the unencrypted value of a SecureString will be stored in the raw state as plain-text" ([Terraform d/ssm_parameter](https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/website/docs/d/ssm_parameter.html.markdown)). `sensitive` only hides it from the CLI. The pattern is:

1. Create the parameter out-of-band with `aws ssm put-parameter --type SecureString`.
2. Build its ARN as a string in Terraform.
3. Pass only the parameter name to Lambda.
4. Fetch it at runtime with `get_parameter(WithDecryption=True)` and a module-level TTL cache.

Grant `ssm:GetParameter` on that one ARN, plus `kms:Decrypt` conditioned on `kms:EncryptionContext:PARAMETER_ARN`, which is the least-privilege example AWS documents ([AWS KMS: Parameter Store](https://docs.aws.amazon.com/kms/latest/developerguide/services-parameter-store.html)). Terraform 1.11 write-only arguments can keep values out of state ([HashiCorp write-only](https://developer.hashicorp.com/terraform/language/manage-sensitive-data/write-only)). Here they belong in the README as a considered-and-rejected alternative: the spec says out-of-band, and Terraform-owned parameters would be deleted by `destroy`. The Parameters and Secrets Lambda Extension also works, but it adds a layer ARN that varies by region, architecture and version ([AWS SSM extension docs](https://docs.aws.amazon.com/systems-manager/latest/userguide/ps-integration-lambda-extensions.html)). That is more drift surface for no gain.

Function URLs changed in October 2025. **New URLs require both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`.** For a same-account `AWS_IAM` caller, the caller's identity policy alone can grant both, using the condition keys `lambda:FunctionUrlAuthType` and `lambda:InvokedViaFunctionUrl`. With `NONE`, a public resource policy is always required, and missing permissions return 403 ([Lambda: function URL auth](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html)). A CDK issue reports that the temporary exception for older single-permission setups ends **1 November 2026** ([aws-cdk #35872](https://www.goodfirstissue.org/aws/aws-cdk/issues/35872)). That date is not from an AWS primary source, and it does not affect a URL created now, which needs both permissions from day one. The provider's `aws_lambda_permission` now exposes `function_url_auth_type` and `invoked_via_function_url` ([Terraform r/lambda_permission](https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/website/docs/r/lambda_permission.html.markdown)), but which provider version added them is unverified. Granting through the identity policy sidesteps that.

Function URLs use payload format 2.0. **`pathParameters` is null**, so routes are parsed from `rawPath` ([Lambda: URL invocation](https://docs.aws.amazon.com/lambda/latest/dg/urls-invocation.html)). URLs attach only to `$LATEST` or an alias. They throttle with 429 at 10 × reserved concurrency RPS, and **reserved concurrency of 0 disables the URL** ([Lambda: URL configuration](https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html)).

The common drift and orphan traps in this stack, and their fixes:

- **Lambda log groups.** Lambda auto-creates `/aws/lambda/<fn>` with infinite retention if the function runs before Terraform creates the group, and `destroy` then leaves it behind. Pre-create the group with `retention_in_days` and use `depends_on` or `logging_config`, as the provider's own example does ([Terraform r/lambda_function](https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/website/docs/r/lambda_function.html.markdown)). Do not grant `logs:CreateLogGroup`, so ordering bugs fail loudly.
- **SQS attributes.** `redrive_policy` and `sqs_managed_sse_enabled` are drift-checked only when present in config, and the docs prefer the dedicated redrive-policy resource ([Terraform r/sqs_queue](https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/website/docs/r/sqs_queue.html.markdown)). Set SSE explicitly and write `maxReceiveCount` as an integer.
- **Unconfirmed SNS email subscriptions** cannot be deleted by Terraform ([Terraform r/sns_topic_subscription](https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/website/docs/r/sns_topic_subscription.html.markdown)). Click the confirmation link before running the destroy test.
- **Budget start date.** Never put `timestamp()` in a Budget's `time_period_start` ([Terraform r/budgets_budget](https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/website/docs/r/budgets_budget.html.markdown)); it changes on every plan.
- **Budgets to SNS** needs a topic policy for `budgets.amazonaws.com`, and the topic must not use SSE with the AWS-managed SNS key, because Budgets cannot publish to it ([AWS Budgets SNS policy](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-sns-policy.html)). That forces a justified suppression of Checkov's SNS-encryption check (CKV_AWS_26).
- **Provider pinning.** Pin the AWS provider `~> 6.x` (GA June 2025, with breaking changes ([HashiCorp blog](https://www.hashicorp.com/blog/terraform-aws-provider-6-0-now-generally-available))). Commit a multi-platform `.terraform.lock.hcl` and build IAM with `aws_iam_policy_document`.

Least privilege comes down to one role per function, exact actions on exact ARNs, and **no `cloudwatch:PutMetricData`**. That action can only be restricted with `Resource: "*"` plus a namespace condition ([CloudWatch namespace condition keys](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/iam-cw-condition-keys-namespace.html)). Embedded Metric Format (EMF) avoids it entirely: metrics are JSON log lines that CloudWatch extracts, and "each DimensionSet creates a new metric", so job IDs belong in root fields, never in dimensions ([EMF specification](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html)). Expect Checkov ([policy index](https://www.checkov.io/5.Policy%20Index/terraform.html)) to flag X-Ray (CKV_AWS_50), function-level concurrency (CKV_AWS_115), Lambda DLQ (CKV_AWS_116), VPC (CKV_AWS_117), DynamoDB CMK (CKV_AWS_119), PITR (CKV_AWS_28) and SNS encryption (CKV_AWS_26). Suppress each one inline with a reason tied to cost or architecture, and list them all in the README. Graders reward reasoned exceptions over silent ones.

CI needs no AWS credentials to meet the spec: ruff, pytest with `--cov-fail-under=80`, `terraform fmt -check`, `init -backend=false && validate`, tflint with the AWS ruleset, and Checkov or `trivy config` (tfsec now lives in Trivy ([migration guide](https://github.com/aquasecurity/tfsec/blob/master/tfsec-to-trivy-migration-guide.md))). If deploys from CI are added later, use OIDC. GitLab supports it via `id_tokens` and `assume-role-with-web-identity`, but only if the instance's OIDC discovery endpoint is publicly reachable ([GitLab OIDC with AWS](https://docs.gitlab.com/ci/cloud_services/aws/)).

## Risks and unknowns to clear in the first hours

| # | Risk / unknown | Why it matters | How to verify early | Fallback |
|---|---|---|---|---|
| 1 | **Qrypt self-registration and token issuance** | Docs conflict: self-service signup vs "contact Qrypt" ([Getting Started](https://docs.qrypt.com/getting_started) vs [EaaS](https://docs.qrypt.com/eaas/)). Without a token nothing runs | Register at portal.qrypt.com on hour 0; generate an Entropy-only token; note the expiry | Email support immediately; build against the fake `EntropySource` meanwhile |
| 2 | **Qrypt monthly quota size** | 403 makes the platform dead until the anniversary reset ([FAQs](https://docs.qrypt.com/faqs/)) | Read the portal usage page after signup | Keep `size=1`; alarm on 403; ask support for headroom |
| 3 | **Qrypt latency vs 800 ms p95** | No published figures; sandbox could not measure | Deploy a probe Lambda in the target region; 50 calls at 1 KiB; record p50/p95 cold and warm | Pre-fetched entropy pool in DynamoDB (still Qrypt-only, fail closed when empty), documented as a trade-off |
| 4 | **Submit cold start** | At 100 jobs/day many submits are cold: init + SSM decrypt + TLS handshake ([Chariot](https://chariotsolutions.com/blog/post/whats-the-point-of-lambda-snapstart/)) | Read `Init Duration` from REPORT lines; measure p95 over 30+ cold submits | Keep submit NumPy-free, lazy-import boto3 clients, raise memory to 512 MB for CPU; clarify whether p95 is measured warm |
| 5 | **New-account Lambda concurrency (10 or 50)** | "New accounts have reduced concurrency" ([quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html)); reserving concurrency fails below the unreserved floor ([re:Post](https://www.repost.aws/questions/QUUt_z9-LeT2SJ8Ggu_Ylpsw/default-lambda-concurrent-executions-value-only-50)) | `aws lambda get-account-settings` on day 0 | Request an increase via Service Quotas; cap with ESM `MaximumConcurrency` only; suppress CKV_AWS_115 with reason |
| 6 | **SQS idle polling vs 1M requests** | ≈ 648k/month per mapped queue ([re:Post](https://www.repost.aws/questions/QUw66hGkRwSWiBSjkIJhTgOw/how-to-calculate-cost-of-sqs-queue-when-consumed-by-a-batched-lambda)); figure is not in official docs | Watch `NumberOfEmptyReceives` for 24 h after deploy | Exactly one mapping; never map the DLQ |
| 7 | **x86 AVX-512 dispatch breaks replay** | Verified 1-ULP exp differences change shard hashes; Lambda x86 hosts are heterogeneous ([UCC 2025](https://faculty.washington.edu/wlloyd/papers/Cordingly_CPU_Distribution_Manipulation_UCC2025-cr.pdf)) | Log `opt_func_info` exp target and CPU model per invocation; replay one job 20× on Lambda and diff hex | `NPY_DISABLE_CPU_FEATURES` + startup self-check, or arm64, or `math.exp` |
| 8 | **`NPY_DISABLE_CPU_FEATURES` names for the pinned NumPy** | NumPy 2.x renamed features into X86_V2/V3/V4 groups | Check `np.show_runtime()` with the exact pinned wheel in the Lambda base image | Fail closed at init if the dispatch target is wrong |
| 9 | **Function URL dual permission** | Both actions required for new URLs; provider-version support for `invoked_via_function_url` unverified ([urls-auth](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html)) | First signed `awscurl` call right after the first apply | Grant both actions in the caller's identity policy |
| 10 | **Account plan type** | Free-plan accounts are suspended at 6 months and hide overage in credits ([Free Tier FAQ](https://aws.amazon.com/free/free-tier-faqs/)) | Check Billing console | State Paid-plan assumption in README; make the Budget count costs before credits (credit-filter behaviour unverified) |
| 11 | **Lambda "Web Functions" pricing** | New request + active-CPU pricing line; unclear whether Function URLs are classified there or covered by free tier ([Lambda pricing](https://aws.amazon.com/lambda/pricing/)) | Check Cost Explorer usage types after day 1 | Budget alarm catches it; document |
| 12 | **DynamoDB encryption key semantics in Terraform** | `enabled = true` may switch to a KMS-billed AWS-managed key (unverified) | `describe-table` → `SSEDescription` after apply | Omit the block (AWS-owned key, still encrypted) and document |
| 13 | **DLQ alarm timing** | Time-to-DLQ = maxReceiveCount × visibility; SQS metrics lag | Run the forced-failure test on day 1 and time it | Lower worker timeout/visibility; `ChangeMessageVisibility` on caught errors |
| 14 | **GitLab mirroring / OIDC on labs.gauntletai.com** | Push mirrors and public OIDC discovery unverified | Try a push mirror on day 0 | Develop on GitHub directly |
| 15 | **Qrypt rate-window semantics and Retry-After** | Undocumented (fixed vs sliding; header presence) ([EaaS](https://docs.qrypt.com/eaas/)) | Log response headers on the probe | Cap 15/window; bounded jittered backoff; never rely on the header |

## Decisions to make

The table summarises the recommended defaults. The subsections give the options, the tradeoffs and the rationale for each.

| # | Decision | Recommended default |
|---|---|---|
| D1 | Function language/runtime | Python 3.13 + NumPy 2.5.x pinned with hashes |
| D2 | Lambda architecture and SIMD | x86_64 with `NPY_DISABLE_CPU_FEATURES` + fail-closed startup self-check + fingerprint per job |
| D3 | Master-seed derivation | HKDF-SHA256 over 1 KiB, fixed salt, versioned `info` |
| D4 | Shard-seed derivation | `SeedSequence(master_int, spawn_key=(i,))` → `PCG64DXSM` |
| D5 | Function URL auth | `AWS_IAM`, both permissions in the caller's identity policy |
| D6 | Routing | One `api` router Lambda (submit, status, replay, health); separate worker |
| D7 | DynamoDB design and capacity | Single table, provisioned 10/10, no GSI, no autoscaling, TTL, PITR off |
| D8 | Fan-in / completion | Conditional shard put + Number-Set ADD + conditional finalise; lazy aggregate on GET |
| D9 | PARTIAL detection | Final-attempt marking + deadline reaper on GET; DLQ untouched, alarmed |
| D10 | Qrypt rate limiting | DynamoDB fixed window, cap 15; synchronous fail-fast 429/503; PENDING_ENTROPY only if no second mapping |
| D11 | Metrics | Hand-rolled EMF, ≤ 8 metrics, no dimensions |
| D12 | Concurrency controls | ESM `MaximumConcurrency`; reserved concurrency only if quota allows |
| D13 | Packaging | Vendored NumPy in worker zip via `pip --platform --only-binary --require-hashes`; NumPy-free API zip; no layers |
| D14 | Shard sizing and memory | 10 × 100k default, worker 1,024 MB / 15–30 s timeout, API 512 MB |
| D15 | Variance reduction | Plain MC; antithetic as optional stretch |
| D16 | SQS/DLQ parameters | Standard queue, batch 1, visibility 6× timeout, maxReceiveCount 3 (documented) |
| D17 | Encryption and KMS | SSE-SQS, AWS-owned DynamoDB key, `aws/ssm` for the secret, no CMKs |
| D18 | X-Ray | Off; suppress CKV_AWS_50 with reason |
| D19 | CI platform | GitHub Actions on a GitHub repo (GitLab push-mirror if work lives on GitLab); no deploy in CI |
| D20 | Terraform state and versions | Local state, TF ≥ 1.11 (1.10 fine), AWS provider `~> 6.x`, five local modules |
| D21 | Stretch goals | Dashboard, replay diff report, antithetic; skip Step Functions and ephemeral envs |

### D1. Function language

The options are Python + NumPy, Go, Rust and Node. Python gives correct `SeedSequence` spawn semantics out of the box, about 25 ms per 1M paths, a roughly 17 MB wheel ([PyPI numpy](https://pypi.org/project/numpy/#files)), and mature pytest/coverage tooling for the 80% gate. Its weakness is the CPU-dependent `exp`, which D2 handles. Go's pure-Go `math.Exp` avoids libm and SIMD dispatch, but the spec lets compilers fuse `x*y+z` into FMA, and arm64 does so ([Go spec](https://go.dev/ref/spec#Floating_point_operators)). Go also means hand-rolling the seed derivation. Rust has the strongest determinism story (the pure-Rust `libm` crate and locked crate versions), but cross-compilation and coverage tooling are friction in two days. Node has no built-in seedable normal sampler. **Default: Python 3.13** on Amazon Linux 2023, which is supported until June 2029 ([Lambda runtimes](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html)), with NumPy pinned to an exact version and hash, because it has the lowest delivery risk. Keep all numerics in a pure `mc_core.py` module so most of the coverage comes from cheap unit tests.

### D2. Lambda architecture (x86_64 vs arm64) and the NumPy SIMD/replay issue

There are three options:

- **x86_64 with `NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR"`** set as a Lambda environment variable. This is verified locally to force `exp` onto a path that matches glibc in 100% of elements. Pair it with an init-time check of `numpy.lib.introspect.opt_func_info` that fails closed if the float64 `exp` target is wrong.
- **arm64.** AWS documents a single CPU, Graviton2, and arm64 is about 20% cheaper per GB-s. But the aarch64 `exp` kernel was not tested, a future Graviton generation with SVE could change dispatch, and golden-hash tests need an arm64 environment (QEMU or Graviton CI), since neither GitHub's x86 runners nor macOS libm will match.
- **`math.exp` over a Python list** at 0.082 s per 1M. This avoids NumPy's transcendental kernels entirely but depends on the glibc version.

Cost is irrelevant, because both architectures sit inside the free tier. **Default: x86_64 with pinned dispatch and the self-check.** It is the only option whose mitigation was verified experimentally, and CI runners are x86, so golden-hash tests can run natively in `public.ecr.aws/lambda/python:3.13`. Either way, record the architecture, NumPy version, dispatch target, `platform.libc_ver()` and CPU model on every job, and make replay compare them. If the self-check proves brittle, switch the exp step to `math.exp` rather than changing architecture.

### D3. Master-seed derivation

There are three options:

- Take the first 32 bytes. This assumes full entropy, which nobody has published evidence for.
- SHA-256 over `domain_tag || 1 KiB`. This is simple and well conditioned.
- HKDF-SHA256 Extract+Expand with a fixed public salt and `info = "qmc/master-seed/v1|" + job_id` ([RFC 5869](https://www.rfc-editor.org/rfc/rfc5869)). It conditions just as well and adds explicit domain separation and versioning.

**Default: HKDF-SHA256**, about 10 stdlib lines, with SHA-256 as an acceptable simpler variant. Store `kdf` and `kdf_version` with the job. Do not mix local entropy (see the Qrypt section).

### D4. Shard-seed derivation

There are two options. **`SeedSequence(master_int, spawn_key=(i,))` with `Generator(PCG64DXSM(...))`** gives NumPy-guaranteed independence, verified equivalence to `spawn()`, and the fastest generator in the notes' benchmark (25 ms per 1M vs 32 ms for Philox). The alternative is **HKDF-Expand(master, "shard|i") → Philox key**, which is language-neutral at the seed step but still tied to NumPy's normal sampler ([NumPy parallel RNG](https://numpy.org/doc/stable/reference/random/parallel.html)). **Default: SeedSequence + PCG64DXSM** with a fixed chunk size. Decide `pool_size` once and freeze it: the default 4 gives 128-bit state, while 8 changes every stream. Never `master + i`.

### D5. Function URL auth

**AWS_IAM** means no secret to rotate, no custom auth code, CloudTrail-attributable callers, and a pass on scanners' Function-URL-auth checks. It needs a SigV4 client (`awscurl` or `botocore.auth.SigV4Auth`; service name `lambda`, from training knowledge) and both invoke permissions ([urls-auth](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html)). **NONE + bearer** is easy to curl but needs a public `Principal: "*"` resource policy for both actions, a second out-of-band secret, a constant-time compare, header redaction in logs, and a justified scanner suppression. **Default: AWS_IAM**, with a Terraform-managed `qmc-<env>-client` role or user that holds only the two conditioned invoke statements. Ship a 20-line Python client script so graders can call it.

### D6. Routing via one router Lambda

One URL attaches to one function alias or `$LATEST`, and `pathParameters` is null. A single `api` router dispatching on `(method, rawPath)` therefore serves `POST /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/replay` and `GET /health` with one URL, one role and one log group ([URL invocation](https://docs.aws.amazon.com/lambda/latest/dg/urls-invocation.html)). Separate functions give per-endpoint IAM (status reads would not need SSM) at the cost of multiple hostnames and about three times the Terraform. Request counts are the same either way. **Default: one router** for the API plus a separate worker. Document that the router's SSM permission is scoped to one parameter ARN. If a reviewer pushes on least privilege, split read-only status into its own function in an hour.

### D7. DynamoDB table design and capacity

The free tier covers **provisioned mode only**, and the 25/25 pool is shared by every table and GSI in the region ([DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/provisioned/)). On-demand avoids capacity planning and autoscaling drift but is not $0. Single-table (`pk`, `sk`) keeps IAM to one ARN. Separate jobs and shards tables are more readable but split the capacity pool. A GSI is only needed for job listing, which is not a core endpoint. **Default: a single table, PROVISIONED 10 RCU / 10 WCU, no autoscaling, no GSI, TTL on `ttl`, PITR off (CKV_AWS_28 suppressed with the cost reason), `deletion_protection_enabled = false`.** 10/10 rather than 25/25 leaves room for a second stack, such as a dev environment, in the same account and region. The 300 s burst bucket (3,000 writes) plus boto3 retries absorbs a 10-shard completion spike of about 20 writes per second. Store results as hex strings, with a display-only Number via `Decimal(repr(x))` if useful.

### D8. Fan-in / completion detection

| Option | Correct under duplicates + crash | WCU/job | Verdict |
|---|---|---|---|
| Put + `ADD completed :1` | No | ~21 | Avoid |
| `TransactWriteItems` put + counter | Yes | ~41 | Costly; can't return the new count |
| **Put + Number-Set `ADD done_shards` + conditional finalise** | **Yes** | **~21** | **Default** |
| Embedded results map on the job item | Yes | ~11 | Conflicts with the shard-item-per-(job, shard) requirement |
| Strongly consistent Query after own write | Yes | ~11 + reads | Good backup; eventually consistent reads can strand jobs |
| DynamoDB Streams aggregator | Yes | ~11 + | Extra function, IAM (`ListStreams` may need `*`), destroy surface |
| Step Functions inline Map | Yes | same + 25–40 transitions | ≈ 100–160 jobs/month inside free tier ([pricing](https://aws.amazon.com/step-functions/pricing/)) |

**Default: the Number-Set pattern**, plus lazy aggregation on GET as a repair path. The finalise write is conditional on `status = RUNNING` and on `failed_shards` being empty, so COMPLETE and PARTIAL stay mutually exclusive.

### D9. PARTIAL detection vs DLQ alarm

The options are: an alarm only (the job stays RUNNING forever, which violates the spec); a DLQ consumer (exact, but it drains the DLQ and can stop the alarm from ever firing); worker final-attempt marking via `ApproximateReceiveCount` (prompt, but misses crashes and timeouts on the last attempt); and a deadline reaper (catches everything, but only after the deadline). **Default: final-attempt marking + a deadline reaper evaluated on GET**, with `deadline_at = submitted_at + maxReceiveCount × visibility + slack`. The DLQ is never consumed and is alarmed on `ApproximateNumberOfMessagesVisible > 0` (Maximum, 60 s, `treat_missing_data = notBreaching`). Make PARTIAL immutable and document it, which is simpler than allowing PARTIAL to become COMPLETE after a manual redrive. A scheduled EventBridge sweep is an optional extra. Its pricing was not researched, so verify it is free before adding it.

### D10. Rate limiting and the PENDING_ENTROPY stretch

The options are: a DynamoDB fixed window (1 WCU per submit; **cap 15** guarantees at most 30 per rolling 10 s); a token bucket (needs capacity + rate × 10 ≤ 30 and optimistic locking); a sliding log (RCU-heavy, and TTL is lazy); and reserved concurrency (not a rate limiter). **Default: a fixed window with cap 12–15**, counting retries too, and returning 429/503 with `Retry-After` and a `QRYPT_RATE_LIMITED` / `QRYPT_UNAVAILABLE` code when over budget. Never sleep in the handler. Allow at most one jittered retry on 429/503/5xx, and only within about 600 ms of total elapsed time. Never retry 400, 401 or 403.

For PENDING_ENTROPY, the design in the notes (write `PENDING_ENTROPY`, return 202, delay-queue to a capped consumer, DLQ to FAILED) adds **a second mapped queue, about 648k more SQS requests, and pushes the total to about 1.37M a month, which breaks $0**. If you attempt it, either reuse the single work queue with a `type` discriminator (the worker role then needs SSM and Qrypt access, which weakens least privilege), or accept and document the roughly $0.15 a month. **Default: do not attempt PENDING_ENTROPY in two days.** The fail-fast path already satisfies fail-closed.

### D11. Metrics via EMF vs PutMetricData

`PutMetricData` needs `Resource: "*"` plus a namespace condition ([CloudWatch condition keys](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/iam-cw-condition-keys-namespace.html)), which breaks a strict no-wildcard rule, and it adds an API call per invocation. EMF needs only the log permissions the function already has ([EMF spec](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html)). Powertools Metrics produces EMF but adds a layer ARN or a dependency. **Default: a hand-rolled EMF emitter (about 10 lines)** in namespace `QMC/<env>` with no dimensions. Use about eight metrics: JobsSubmitted, JobsCompleted, JobsPartial, JobsFailedSeeding, QryptLatencyMs, QryptThrottled, ShardDurationMs and PathsSimulated. Use about nine alarms: DLQ depth, worker Errors, API Errors, worker Throttles, JobsPartial, JobsFailedSeeding, Qrypt auth/quota failures, main-queue AgeOfOldestMessage, and DynamoDB ThrottledRequests. All alarms are standard resolution and stay at 10 or fewer, counting every environment.

### D12. Concurrency controls on a new low-quota account

Reserved concurrency fails if it pushes unreserved concurrency below the floor, which makes it impossible at a 10 or 50 quota ([re:Post](https://www.repost.aws/questions/QUUt_z9-LeT2SJ8Ggu_Ylpsw/default-lambda-concurrent-executions-value-only-50)). Reserved concurrency on its own also throttles SQS messages into the DLQ ([Compute Blog](https://aws.amazon.com/blogs/compute/introducing-maximum-concurrency-of-aws-lambda-functions-when-using-amazon-sqs-as-an-event-source/)). Setting `MaximumConcurrency` disables the idle scale-down to 2 pollers, which costs SQS headroom (already budgeted above). **Default:** request a quota increase on day 0. Set ESM `maximum_concurrency` to 5 on a 10-quota account (leaving room for the API) or 10 otherwise. Make reserved concurrency a variable defaulting to null, set it to at least the ESM maximum (and at least 5) only once the quota allows, and suppress CKV_AWS_115 with the reason until then. Never set the API's reserved concurrency to 0, because that disables the URL.

### D13. Lambda packaging

The options are:

- **Vendored NumPy in the worker zip.** About a 20 MB zip, under the 50 MB direct-upload and 250 MB unzipped limits ([quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html)), and one artifact with no layer drift.
- **A self-built layer.** Shareable, but adds a version resource.
- **The AWS SDK for pandas layer.** AWS chooses its NumPy version, which is bad for replay pinning.
- **Container images.** ECR storage may not be free on new accounts (unverified).

**Default:** build outside Terraform with `pip install --platform manylinux_2_28_x86_64 --implementation cp --python-version 3.13 --only-binary=:all: --require-hashes -r requirements.lock`, set `PYTHONDONTWRITEBYTECODE=1`, exclude `__pycache__`, and feed `archive_file.output_base64sha256` to `source_code_hash`. The API zip contains no NumPy, for fast cold starts.

### D14. Shard sizing and memory

Compute per 100k-path shard is milliseconds, so init and per-invocation overhead dominate, and fewer shards are cheaper. More shards demonstrate fan-out and shorten wall time slightly, but add WCU and cold starts. **Default: `n_shards = 10` (validated range 1–50), 100k paths per shard for a 1M-path job, chunk 2¹⁷, worker 1,024 MB with a 15–30 s timeout, API 512 MB** (more CPU for TLS and boto3 init, still trivially free). Store `n_shards`, `paths_per_shard` and `chunk_size`, because replay with different sharding gives a different, though equally valid, estimate.

### D15. Variance reduction

The options are plain MC; antithetic variates (about 2× variance reduction, measured SE 0.0104 vs 0.0147); and a control variate on discounted S_T (about 6.9× reduction, SE 0.0056). Antithetic fits the {sum, sum_sq, n} contract only if the sample unit is the pair average, so `n` counts pairs and SE must be computed over pair means. The naive per-payoff SE overstates it. Control variates need extra cross-moments (Σx, Σx², Σxy) or a pre-declared fixed β. **Default: plain MC.** It already meets the 3-SE test with a trivially auditable SE. Antithetic is a cheap stretch if the record documents `n = pairs`.

### D16. SQS and DLQ parameters

AWS recommends `maxReceiveCount ≥ 5` ([configure ESM](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html)), mainly to absorb throttle-induced receives. With `MaximumConcurrency` set those mostly disappear, and a lower value makes the forced-failure demo reach the DLQ faster. **Default: standard queue, SSE-SQS, batch 1, window 0, `ReportBatchItemFailures`, worker timeout 30 s, visibility 180 s, `maxReceiveCount = 3`** (about 9 minutes to the DLQ), with the deviation from AWS guidance documented. Main-queue retention is 1 day and DLQ retention 14 days. Use `aws_sqs_queue_redrive_policy` with an integer `maxReceiveCount`. To speed retries after a caught error, the worker can call `ChangeMessageVisibility` to about 10 s, which needs that permission on the main queue.

### D17. Encryption and KMS

Customer-managed keys cost $1 a month each ([KMS pricing](https://aws.amazon.com/kms/pricing/)). An AWS-managed SNS key blocks Budgets and CloudWatch from publishing ([Budgets SNS policy](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-sns-policy.html)). **Default:** SSE-SQS on both queues, the DynamoDB default AWS-owned key (verify in `describe-table`), the `aws/ssm` key for the SecureString with an encryption-context-scoped `kms:Decrypt`, and an unencrypted alerts SNS topic. Suppress the CMK checks (CKV_AWS_119 and its SNS, log-group and Lambda env-var equivalents) with the always-free reason.

### D18. X-Ray

X-Ray is free under 100k traces a month ([CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)), but its write actions reportedly need `Resource: "*"` (training knowledge). **Default: off**, suppressing CKV_AWS_50 with the no-wildcard reason. Structured JSON logs carrying `job_id` and `shard_index`, plus Logs Insights, cover the debugging need.

### D19. CI platform: GitLab vs GitHub, and mirroring

The research notes state that the deliverable is a GitHub repository. GitLab (labs.gauntletai.com) push-mirroring and public OIDC discovery are unverified there. **Default: GitHub Actions on the GitHub repo.** Run two jobs, python (ruff, pytest with coverage ≥ 80%) and terraform (fmt, validate, tflint with the AWS plugin, Checkov). Give the workflow `permissions: contents: read` and no AWS credentials. Add a golden-hash replay test that runs inside the Lambda Python 3.13 x86_64 image. If day-to-day work lives on GitLab, configure a push mirror to GitHub on day 0 and keep a matching `.gitlab-ci.yml`. Leave deploying from CI out of scope, because it needs remote state and OIDC bootstrapping.

### D20. Terraform state, versions and layout

**Default:** local state, which suits a single-operator graded project. Never commit `*.tfstate*` or `*.tfvars`. Commit `.terraform.lock.hcl` with linux and darwin hashes. Require TF ≥ 1.11 (or 1.10) and AWS provider `~> 6.x`, and use `default_tags` with `Project=qmc` so orphans can be found with the tagging API. Use five local modules (data, api, workers, observability, budget) with documented variables and outputs. Gate the Budget with `count` so a second environment does not duplicate it. A no-drift script runs `apply`, then `plan -detailed-exitcode` (expect exit code 0), then `destroy`, then a check for leftover tags and leftover `/aws/lambda/` log groups. Apply time should be 1–3 minutes, well under 10, because there is no VPC, NAT or CloudFront.

### D21. Which stretch goals to attempt

| Stretch | Effort | Free-tier impact | Recommend |
|---|---|---|---|
| CloudWatch dashboard (1 of 3 free) | Low | None | **Yes** |
| Replay diff report (`bit_identical`, per-shard hex diffs, fingerprint compare) | Low | None | **Yes**: it showcases the hardest requirement |
| Antithetic variates | Low | None | Yes, if time remains |
| Pre-fetched entropy pool | Medium | Scheduled trigger cost unverified | Only if the Qrypt p95 measurement forces it |
| PENDING_ENTROPY | Medium-high | +≈ 648k SQS req/month → over 1M | No (or single-queue variant) |
| Step Functions orchestration | Medium | ≈ $0.31/month at 3,000 jobs | No |
| Ephemeral per-branch environments | High | Doubles metrics and DynamoDB pool use; needs S3 state | No |
| CI deploy via OIDC | Medium | S3 state cost (cents) | No |

## Conclusion

The research turns the project around. Its defining engineering problem is not quantum entropy or parallel Monte Carlo, both of which are thin layers over documented primitives. It is **making "bit-identical" an honest, testable claim** on hardware AWS does not let you choose. The practical answer is to treat the execution environment as part of the seed: pin the NumPy build and SIMD target, refuse to run when they drift, record the fingerprint on every job, and have replay report a byte-level comparison rather than assume one. The same approach applies to Qrypt. Trust it as the sole entropy source, but record enough provenance (the raw-block hash, KDF version and HTTP metadata) that a reviewer can audit every seed. Fail visibly whenever that trust cannot be established.

For planning the two days, the critical path is external and empirical, not code. The Qrypt token, the Qrypt latency from your region, the account's Lambda concurrency quota, and one replay-20-times-and-diff run on real Lambda hardware are the four facts that can invalidate a design choice. All four can be checked before the main build starts. The $0 constraint behaves the same way: it is decided by counts, not by load. That means one SQS mapping, provisioned DynamoDB with no autoscaling, ten or fewer metrics and alarms across all environments, and no customer-managed keys. Choosing those on day 0 means the cost guardrails never have to fire.

# Demo script (≤ 5 minutes)

The video covers the assignment's five segments: `terraform apply`, a job running to completion, the Black-Scholes comparison, a bit-identical replay, and the DLQ alarm firing. It is recorded during the G4 final sequence (HANDOFF §2), from a **fresh clone from GitHub**, following only the README. Cut every wait; show a timestamp or the terminal's clock wherever a wait is cut.

## Order on the day

| Step | What | Recorded? |
| --- | --- | --- |
| 1 | If a grader access key exists, delete it by CLI (D44), then `make destroy` from the `main` checkout | No |
| 2 | Fresh clone from GitHub into a new folder; `make setup`; `make build && make apply` from empty, timed | **Yes: segment 1** |
| 3 | Confirm the SNS subscription email at once (alarm emails are dropped until it is confirmed) | Yes, a few seconds |
| 4 | `make warm-dlq`, and note the time. Segment 5 starts **at least 15 minutes later** (R23): the DLQ's first metrics can otherwise take 15 minutes to appear | No |
| 5 | Segments 2–4, then segment 5 once the 15 minutes have passed | **Yes** |
| 6 | Create the grader access key by CLI **last**, and send it privately | No |
| 7 | Freeze: no apply or destroy until grading ends | — |

## Segments

Expected durations come from the G1–G3 drills (docs/TEST_RESULTS.md, docs/BENCHMARKS.md).

| # | Segment | Command | Expect | Target on video |
| --- | --- | --- | --- | --- |
| 0 | Title: what it does | README top, architecture diagram | — | 0:15 |
| 1 | `terraform apply` from empty, sped up | `make build && make apply` | About 110 s and 37 resources, with the watcher and grader user on (fresh-clone rehearsal, 2026-10-15). End on the outputs (`api_url`) | 0:40 |
| 1b | SNS confirmation | Click "Confirm subscription" in the email | "Subscription confirmed!" | 0:10 |
| 2 | A 1M-path job running to COMPLETE | `uv run python clients/qmc.py submit --S0 100 --K 100 --r 0.05 --sigma 0.2 --T 1 --paths 1000000 --shards 10`, then `… wait <job_id>` | 202 with `master_seed_hex`; COMPLETE in about 1–3 s from submit. `wait` may see COMPLETE on its first poll | 0:40 |
| 3 | The Black-Scholes comparison | `uv run python clients/qmc.py get <job_id>` | `result.estimate`, `std_error`, `ci95`; `reference.black_scholes` 10.450583572185565, `z_score`, `within_3se: true`; `seed_provenance` (Qrypt, `raw_sha256`) | 0:35 |
| 4 | A bit-identical replay | `… replay <job_id>`, then `… wait <replay_id>` | `replay_check.bit_identical: true`, `shards_compared: 10`, `shard_diffs: []`, `fingerprint_match: true` | 0:35 |
| 5 | Forced failure → PARTIAL → DLQ → alarm → email | `make force-failure` (needs `enable_fault_injection = true`) | PARTIAL with `failed_shards: [3]` at about +13 s; DLQ depth 1 at about +23 s; `qmc-dev-dlq-depth` ALARM at about +4 min (237 s in the G1 drill), shown live on the CloudWatch alarms page; then the email as the saved screenshot `docs/images/dlq-alarm-email.png` (the G1 drill's alarm at 2026-10-13 11:48:08 UTC), not a live inbox (James, 2026-10-15) | 1:10 |
| 6 | Close: guardrails | The `qmc-dev` dashboard and the Budget page ($0.00 of $1.00) | — | 0:25 |
| | **Total** | | | **4:10**, leaving 50 s spare |

## Narration

0. "This prices a European call by Monte Carlo on AWS Lambda. Each job is seeded from one block of Qrypt quantum entropy, fanned out over SQS to parallel workers, and can be replayed bit for bit. It all stays inside the AWS always-free tier."
1. "Everything is Terraform. This is a fresh clone of the public repo, applied from an empty account in about a minute. The Qrypt token was stored in SSM beforehand by the CLI, so it never enters Terraform state."
1b. "Alarm emails go through SNS, so I confirm the subscription straight away."
2. "A one-million-path job, split into ten shards. The API took 1 KiB of entropy from Qrypt and derived the master seed. The workers finish in about a second."
3. "The result has the estimate, its standard error and 95% interval, next to the closed-form Black-Scholes price. The z-score is within three standard errors. The seed's provenance records which Qrypt block it came from, by hash."
4. "A replay reruns the job from its stored seed without calling Qrypt. It compares every shard's sums byte for byte: bit-identical, and the runtime fingerprints match."
5. "Now a forced failure: shard 3 is told to fail. The job ends PARTIAL, naming shard 3, and keeps a partial result from the other nine. After three attempts the message goes to the dead-letter queue, the alarm fires. This is the email the same alarm sent in an earlier drill." *(Cut the wait for the alarm; show the alarm page live, then the screenshot.)*
6. "The guardrails: seven alarms, eight custom metrics on this dashboard, and a $1 Budget that alerts at the first cent. The bill so far is zero."

## Before recording

- [ ] No Qrypt token, access key or secret on screen. Do not open `~/.qmc/dev.tfvars` on camera: it holds the alert email address. Do not open the inbox: segment 5 uses the saved email screenshot
- [ ] Recording from the fresh clone, on `main`, with `git log -1` matching GitHub's `main`
- [ ] SNS subscription Confirmed (step 3)
- [ ] `make warm-dlq` at least 15 minutes before segment 5, and `qmc-dev-dlq-depth` is OK
- [ ] `enable_fault_injection = true` in `~/.qmc/dev.tfvars` (segment 5)
- [ ] Each submit spends 1 KiB of the Qrypt quota (segment 5 too); start submits at least 2 s apart
- [ ] Final length ≤ 5:00; link it from the README

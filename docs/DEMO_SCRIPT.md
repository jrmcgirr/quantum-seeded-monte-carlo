# Demo script (≤ 5 minutes)

> **Skeleton (QMC-150).** Final at QMC-156. Run `make warm-dlq` at least 15 minutes before segment 5. Cut every wait.

| # | Segment | Command | Target length |
| --- | --- | --- | --- |
| 1 | `terraform apply` from empty (sped up; recorded during the QMC-157 rehearsal) | `make build && make apply` | *TBD* |
| 2 | A 1M-path job running to COMPLETE | `make e2e` or `clients/qmc.py submit` + `wait` | *TBD* |
| 3 | The Black-Scholes comparison in the result | `clients/qmc.py get <id>` | *TBD* |
| 4 | A bit-identical replay | `clients/qmc.py replay <id>` + `wait` | *TBD* |
| 5 | Forced failure → PARTIAL → DLQ alarm → email | `make force-failure` | *TBD* |

## Narration

*TBD*

## Before recording

- [ ] No token, access key or unblurred secret on screen
- [ ] SNS subscription Confirmed
- [ ] `make warm-dlq` ≥ 15 minutes before segment 5

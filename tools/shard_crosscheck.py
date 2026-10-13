"""Per-shard bit check of a deployed job against a recompute here (I5 evidence across hardware).

  fetch JOB_ID > job.json   real-Lambda shard sums from DynamoDB (operator credentials)
  check job.json            recompute every shard with qmc_core and compare hex; exit 1 on any diff

``check`` needs only qmc_core + NumPy, so it also runs in the Lambda image under Docker:
  docker run --rm --platform linux/amd64 -v "$PWD":/repo:ro -w /repo --entrypoint /bin/bash \\
    -e NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR" public.ecr.aws/lambda/python:3.13 -c \\
    'pip install -q --target /tmp/d --require-hashes -r requirements/worker.txt &&
     PYTHONPATH=/tmp/d:/repo/src python tools/shard_crosscheck.py check job.json'
"""

from __future__ import annotations

import json
import platform
import sys


def fetch(job_id: str) -> None:
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from _common import aws, outputs

    table = outputs()["table_name"]
    job = aws("dynamodb").get_item(
        TableName=table,
        ConsistentRead=True,
        Key={"pk": {"S": f"JOB#{job_id}"}, "sk": {"S": "META"}},
    )["Item"]
    items = aws("dynamodb").query(
        TableName=table,
        ConsistentRead=True,
        KeyConditionExpression="pk = :p AND begins_with(sk, :s)",
        ExpressionAttributeValues={":p": {"S": f"JOB#{job_id}"}, ":s": {"S": "SHARD#"}},
    )["Items"]
    json.dump(
        {
            "job_id": job_id,
            "master_seed_hex": job["master_seed_hex"]["S"],
            "params": {k: v["S"] for k, v in job["params"]["M"].items()},
            "chunk_size": int(job["chunk_size"]["N"]),
            "variance_reduction": job.get("variance_reduction", {}).get("S", "none"),
            "shards": [
                {
                    "i": int(s["shard_index"]["N"]),
                    "n": int(s["n"]["N"]),
                    "sum_hex": s["sum_hex"]["S"],
                    "sum_sq_hex": s["sum_sq_hex"]["S"],
                    "cpu_model": s["fingerprint"]["M"]["cpu_model"]["S"],
                    "exp_target": s["fingerprint"]["M"]["exp_target"]["S"],
                }
                for s in items
            ],
        },
        sys.stdout,
        indent=1,
    )


def check(path: str) -> int:
    from qmc_core.codec import params_from_repr, to_hex
    from qmc_core.fingerprint import exp_target
    from qmc_core.seeds import master_int_from_hex
    from qmc_core.sim import simulate_shard

    with open(path) as f:
        job = json.load(f)
    m, params = master_int_from_hex(job["master_seed_hex"]), params_from_repr(job["params"])
    here = f"{platform.machine()} exp={exp_target()} cpu={_cpu()}"
    bad = 0
    for s in sorted(job["shards"], key=lambda s: s["i"]):
        vr = job.get("variance_reduction", "none")
        # A shard item's n counts pairs for antithetic; simulate_shard takes the shard's paths.
        r = simulate_shard(
            params, s["n"] * (2 if vr == "antithetic" else 1), m, s["i"], job["chunk_size"], vr
        )
        ok = (to_hex(r.sum), to_hex(r.sum_sq)) == (s["sum_hex"], s["sum_sq_hex"])
        bad += not ok
        print(
            f"shard {s['i']:2d} {'MATCH' if ok else 'DIFF '} lambda[{s['exp_target']}, {s['cpu_model']}] "
            f"{s['sum_hex']} {s['sum_sq_hex']} | here {to_hex(r.sum)} {to_hex(r.sum_sq)}"
        )
    print(
        f"{job['job_id']}: {len(job['shards']) - bad}/{len(job['shards'])} shards bit-identical; here = {here}"
    )
    return 1 if bad else 0


def _cpu() -> str:
    try:
        return next(
            ln.split(":", 1)[1].strip()
            for ln in open("/proc/cpuinfo")
            if ln.startswith("model name")
        )
    except OSError:
        return platform.processor() or "unknown"


if __name__ == "__main__":
    cmd, arg = sys.argv[1:3]
    sys.exit(fetch(arg) if cmd == "fetch" else check(arg))

"""Deterministic Lambda zips (HANDOFF §8.5).

build/api.zip     qmc_core + qmc_api (no NumPy)
build/worker.zip  qmc_core + qmc_worker + NumPy from the hash-pinned manylinux_2_28_x86_64 wheel
build/manifest.json  git sha, branch, dirty flag, zip hashes (outside the zips)

Same inputs give byte-identical zips: sorted entries, 1980-01-01 timestamps, fixed permissions, no bytecode.
--check builds twice and fails if any hash differs.
"""

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BUILD = ROOT / "build"
REQUIREMENTS = ROOT / "requirements" / "worker.txt"
PIP = "pip==25.2"  # pinned so the installed dist-info is the same on every machine
MAX_ZIP_BYTES = 50 * 1024 * 1024  # Lambda direct-upload limit
EPOCH = (1980, 1, 1, 0, 0, 0)


def _files(base: Path, top: str) -> list[tuple[str, Path]]:
    """(arcname, path) for every file under base/top, skipping bytecode and pip's console scripts."""
    out = []
    for p in (base / top).rglob("*"):
        rel = p.relative_to(base)
        if p.is_dir() or "__pycache__" in rel.parts or p.suffix == ".pyc":
            continue
        out.append((rel.as_posix(), p))
    return out


def _write_zip(dest: Path, entries: list[tuple[str, Path]]) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in sorted(entries):
            info = zipfile.ZipInfo(arcname, date_time=EPOCH)
            info.external_attr = 0o100644 << 16
            info.create_system = 3  # unix, whatever the build host
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes(), compresslevel=6)


def _install_numpy(target: Path) -> None:
    if not REQUIREMENTS.exists():
        sys.exit(f"missing {REQUIREMENTS.relative_to(ROOT)} (WS0, G0a)")
    cmd = [
        "uvx", "--from", PIP, "pip", "install",
        "--platform", "manylinux_2_28_x86_64",
        "--implementation", "cp",
        "--python-version", "3.13",
        "--only-binary=:all:",
        "--require-hashes",
        "--no-compile",
        "--no-deps",
        "--disable-pip-version-check",
        "--target", str(target),
        "-r", str(REQUIREMENTS),
    ]  # fmt: skip
    subprocess.run(cmd, check=True)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def build() -> dict[str, dict[str, str | int]]:
    BUILD.mkdir(exist_ok=True)
    core = _files(SRC, "qmc_core")
    _write_zip(BUILD / "api.zip", core + _files(SRC, "qmc_api"))
    with tempfile.TemporaryDirectory() as tmp:
        deps = Path(tmp)
        _install_numpy(deps)
        vendored = [
            e
            for top in sorted(p.name for p in deps.iterdir() if p.name != "bin")
            for e in _files(deps, top)
        ]
        _write_zip(BUILD / "worker.zip", core + _files(SRC, "qmc_worker") + vendored)

    zips = {}
    for name in ("api.zip", "worker.zip"):
        data = (BUILD / name).read_bytes()
        if len(data) > MAX_ZIP_BYTES:
            sys.exit(f"{name} is {len(data)} bytes, over Lambda's 50 MB upload limit")
        zips[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "sha256_b64": base64.b64encode(
                hashlib.sha256(data).digest()
            ).decode(),  # = source_code_hash
            "bytes": len(data),
        }
    with zipfile.ZipFile(BUILD / "api.zip") as zf:
        if any(n.startswith("numpy/") for n in zf.namelist()):
            sys.exit("api.zip must not contain NumPy")

    manifest = {
        "git_sha": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "zips": zips,
    }
    (BUILD / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return zips


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="build twice and compare hashes")
    args = parser.parse_args()

    first = build()
    if args.check:
        shutil.rmtree(BUILD)
        second = build()
        for name in first:
            same = first[name]["sha256"] == second[name]["sha256"]
            print(
                f"{name}: {first[name]['sha256']} {'identical' if same else 'DIFFERS: ' + str(second[name]['sha256'])}"
            )
            if not same:
                sys.exit(1)
    for name, z in first.items():
        print(f"build/{name} {z['bytes']} bytes sha256={z['sha256']}")


if __name__ == "__main__":
    main()

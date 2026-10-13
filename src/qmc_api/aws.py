"""AWS handles and configuration, created lazily so tests (moto) can set the environment first."""

from __future__ import annotations

import functools
import os

import boto3

# HANDOFF §8.4 defaults for every variable the API reads.
DEFAULTS = {
    "QMC_ENV": "dev",
    "QMC_BUILD_SHA": "unknown",
    "TABLE_NAME": "qmc-dev",
    "QUEUE_URL": "",
    "QRYPT_API_KEY_PARAM": "/qmc/qrypt-api-key",
    "QRYPT_URL": "https://api-eus.qrypt.com/api/v1/quantum-entropy",
    "QRYPT_DEADLINE_MS": "2500",
    "QRYPT_MAX_PER_WINDOW": "15",
    "SECRET_CACHE_TTL_S": "300",
    "JOB_DEADLINE_S": "900",
    "JOB_TTL_DAYS": "365",
    "FAULT_INJECTION_ENABLED": "false",
}


def env(name: str) -> str:
    return os.environ.get(name, DEFAULTS[name])


def env_int(name: str) -> int:
    return int(env(name))


def fault_injection_enabled() -> bool:
    return env("FAULT_INJECTION_ENABLED").lower() == "true"


def config() -> dict[str, str]:
    """Every setting the API reads (no secrets: the token parameter is a name)."""
    return {name: env(name) for name in DEFAULTS}


@functools.cache
def dynamodb():
    return boto3.resource("dynamodb")


def table():
    return dynamodb().Table(env("TABLE_NAME"))


@functools.cache
def sqs():
    return boto3.client("sqs")


@functools.cache
def ssm():
    return boto3.client("ssm")


def reset() -> None:
    """Drop cached clients (tests start a fresh moto backend per test)."""
    for f in (dynamodb, sqs, ssm):
        f.cache_clear()

# Quantum-Seeded Monte Carlo. Final recipes (WS0, G0a): every target calls a fixed path from
# HANDOFF §4; the owning workstream fills the script in. Nobody else edits this file.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

ENV ?= dev
N ?= 20
ARGS ?=
EXTRA_VARS ?=

QMC_HOME := $(HOME)/.qmc
TF_STATE := $(QMC_HOME)/state/$(ENV).tfstate
TF_VARS := $(QMC_HOME)/$(ENV).tfvars
TF := terraform -chdir=infra
LAMBDA_IMAGE := public.ecr.aws/lambda/python:3.13
# Pinned for the exact NumPy in requirements/worker.txt (HANDOFF §12, QMC-099).
NPY_DISABLE_CPU_FEATURES := X86_V4 AVX512_ICL AVX512_SPR
BUILD_SHA = $(shell python3 -c 'import json; print(json.load(open("build/manifest.json"))["git_sha"])')

# $(call need,<path>,<ticket>): stop with "TODO <ticket>" until the owner has written <path>.
need = test -e $(1) || { echo "TODO $(2): $(1) is not written yet"; exit 1; }

.PHONY: help setup fmt lint test test-golden build init plan apply destroy outputs guard-main tfvars \
        e2e bench burst force-failure warm-dlq replay-check drift-check

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## uv sync (Python 3.13)
	uv sync

fmt: ## ruff format + fix, terraform fmt
	uv run ruff format .
	uv run ruff check --fix .
	if [ -d infra ]; then terraform fmt -recursive infra; fi

lint: ## ruff, terraform fmt -check, tflint
	uv run ruff check .
	uv run ruff format --check .
	if [ -d infra ]; then terraform fmt -check -recursive infra && tflint --chdir=infra --init >/dev/null && tflint --chdir=infra --recursive; fi

test: ## unit and integration tests (moto, fake entropy; no AWS, no token)
	uv run pytest -m "not deployed and not lambda_image" --cov --cov-report=term-missing:skip-covered $(ARGS)

test-golden: ## golden-hash tests inside the Lambda Python image (Docker, x86_64)
	docker run --rm --platform linux/amd64 -v "$(CURDIR)":/repo:ro -w /repo --entrypoint /bin/bash \
	  -e QMC_ENFORCE_PINNED_DISPATCH=true -e NPY_DISABLE_CPU_FEATURES="$(NPY_DISABLE_CPU_FEATURES)" \
	  -e PYTHONDONTWRITEBYTECODE=1 $(LAMBDA_IMAGE) -c '\
	    pip install -q --no-compile --target /tmp/deps --require-hashes -r requirements/worker.txt && \
	    pip install -q --no-compile --target /tmp/deps pytest && \
	    PYTHONPATH=/tmp/deps:/repo/src:/repo python -m pytest -p no:cacheprovider -m lambda_image $(ARGS)'

build: ## deterministic build/api.zip, build/worker.zip and build/manifest.json
	@$(call need,scripts/build_lambdas.py,QMC-110)
	uv run python scripts/build_lambdas.py

tfvars:
	@test -f "$(TF_VARS)" || { echo "missing $(TF_VARS) (HANDOFF §2 step 0.7)"; exit 1; }

init:
	@$(call need,infra/versions.tf,QMC-111)
	mkdir -p "$(QMC_HOME)/state"
	$(TF) init -input=false -reconfigure -backend-config="path=$(TF_STATE)" >/dev/null

# D33: apply and destroy refuse a checkout whose HEAD does not contain origin/main.
guard-main:
	@if [ "$${ALLOW_BRANCH_APPLY:-0}" != 1 ]; then \
	  git fetch -q origin main; \
	  git merge-base --is-ancestor origin/main HEAD || { \
	    echo "refusing: HEAD does not contain origin/main (rebase on main, or ALLOW_BRANCH_APPLY=1; HANDOFF D33)"; exit 1; }; \
	fi

plan: tfvars build init ## terraform plan with $HOME/.qmc/$(ENV).tfvars
	$(TF) plan -input=false -var-file="$(TF_VARS)" -var build_sha=$(BUILD_SHA) $(EXTRA_VARS)

apply: guard-main tfvars build init ## terraform apply (main only after G1, D33)
	$(TF) apply -input=false -var-file="$(TF_VARS)" -var build_sha=$(BUILD_SHA) $(EXTRA_VARS)

destroy: guard-main tfvars build init ## terraform destroy (main only after G1, D33)
	$(TF) destroy -input=false -var-file="$(TF_VARS)" -var build_sha=$(BUILD_SHA) $(EXTRA_VARS)

outputs: init ## deployed URL and names (JSON)
	$(TF) output -json

e2e: ## 1M-path job -> COMPLETE -> Black-Scholes -> replay -> bit-identical (ARGS=--fresh after an apply from empty)
	@$(call need,tools/e2e.py,QMC-142)
	uv run python tools/e2e.py $(ARGS)

bench: ## benchmarks -> docs/BENCHMARKS.md
	@$(call need,tools/bench.py,QMC-147)
	uv run python tools/bench.py $(ARGS)

burst: ## 40 concurrent submits vs the 15-per-10 s limiter
	@$(call need,tools/burst.py,QMC-143)
	uv run python tools/burst.py $(ARGS)

force-failure: ## fail_shards=[3] -> PARTIAL, DLQ, alarm (warm the DLQ >= 15 min before)
	@$(call need,tools/force_failure.py,QMC-144)
	uv run python tools/force_failure.py $(ARGS)

warm-dlq: ## any SQS call on the DLQ so its metrics are live (R23)
	@$(call need,tools/warm_dlq.sh,QMC-144)
	bash tools/warm_dlq.sh $(ARGS)

replay-check: ## N replays, all bit-identical (N=20)
	@$(call need,tools/replay_many.py,QMC-146)
	uv run python tools/replay_many.py --n $(N) $(ARGS)

drift-check: ## plan=0 -> destroy -> orphan scan -> apply -> e2e --fresh -> plan=0 (integrator, from main)
	@$(call need,tools/drift_check.sh,QMC-145)
	bash tools/drift_check.sh $(ARGS)

#!/usr/bin/env bash
# G2 ticket 07: run the required Linux backend case files against the freshly built sidecar.
# The files are listed explicitly (no -k, no directory); the collection guard then requires every
# id in tests/cli/g2-required-linux.txt to have passed. The basetemp is outside the checkout (aider
# refuses a git-tracked cwd) and holds every runtime-receipts.jsonl the receipt-hash check reads.
set -euo pipefail

mkdir -p test-results/ci
uv --project backend run pytest \
  backend/tests/test_cli_runtime_composition.py \
  backend/tests/test_cli_runtime_composition_vendors.py \
  backend/tests/test_cli_runtime_observe_vendors.py \
  backend/tests/test_cli_runtime_observe_cost.py \
  backend/tests/test_cli_runtime_recovery.py \
  backend/tests/test_cli_runtime_lifecycle.py \
  backend/tests/test_cli_runtime_handoff.py \
  backend/tests/test_cli_runtime_sidecar.py \
  backend/tests/test_cli_runtime_managed_login.py \
  backend/tests/test_cli_runtime_vendor_transport.py \
  backend/tests/test_cli_runtime_review1.py \
  backend/tests/test_cli_runtime_observe.py \
  backend/tests/test_cli_runtime_claude.py \
  backend/tests/test_cli_runtime_claude_parity.py \
  backend/tests/test_cli_runtime_observe_codex.py \
  backend/tests/test_cli_create_meta_size.py \
  backend/tests/test_cli_runtime_credential_env_remove.py \
  -q -p no:cacheprovider -o junit_family=xunit2 \
  --basetemp "$G2_BASETEMP" \
  --junitxml test-results/ci/g2-linux-junit.xml

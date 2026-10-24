#!/usr/bin/env bash
# G2 ticket 07: build the two Linux sidecars the hosted run uses and export their paths.
#   $RUNNER_TEMP/g2/target          release-shaped sidecar, no test features (the principal runtime)
#   $RUNNER_TEMP/g2/target-harness  fault-hooks harness sidecar (ticket 04's wire-2 fault-hook tests)
# The harness sits at <parent of target>/target-harness/release, where runtime_recovery_driver.py looks.
set -euo pipefail

base="$RUNNER_TEMP/g2"
manifest=native/navide-cli-runtime/Cargo.toml
release_bin="$base/target/release/navide-cli-runtime"
harness_bin="$base/target-harness/release/navide-cli-runtime"

CARGO_TARGET_DIR="$base/target-harness" cargo build --locked --release --manifest-path "$manifest" --features fault-hooks
CARGO_TARGET_DIR="$base/target" cargo build --locked --release --manifest-path "$manifest"

test -x "$release_bin"
test -x "$harness_bin"
file "$release_bin" | grep -q 'ELF 64-bit'

# The proof that the release build does not carry the feature: "fault: exit watcher could not start"
# is an error message that exists only inside #[cfg(feature = "fault-hooks")] code, so it is in the
# harness binary (the positive control) and must be absent from the release binary. Stripping does
# not remove string literals.
node tests/cli/g2Native.mjs proof --release "$release_bin" --harness "$harness_bin"

sha=$(sha256sum "$release_bin" | cut -d' ' -f1)
{
  echo "NAVIDE_CLI_RUNTIME_BIN=$release_bin"
  echo "NAVIDE_T05_SIDECAR=$release_bin"
  echo "NAVIDE_CLI_RUNTIME_HARNESS_BIN=$harness_bin"
  echo "G2_SIDECAR_SHA=$sha"
  echo "G2_BASETEMP=$RUNNER_TEMP/g2-basetemp"
} >> "$GITHUB_ENV"
echo "uname -m: $(uname -m); principal sidecar sha256: $sha"

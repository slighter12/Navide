# G2 ticket 07: build the two Windows sidecars the hosted run uses and export the release one.
#   g2-cargo-target          release-shaped sidecar, no test features (the principal runtime)
#   g2-cargo-target-harness  fault-hooks harness sidecar, built only to prove the feature is absent from the release build
$ErrorActionPreference = 'Stop'
$target = Join-Path $env:RUNNER_TEMP 'g2-cargo-target'
$harnessTarget = Join-Path $env:RUNNER_TEMP 'g2-cargo-target-harness'
$manifest = 'native/navide-cli-runtime/Cargo.toml'

$env:CARGO_TARGET_DIR = $harnessTarget
cargo build --locked --release --manifest-path $manifest --bin navide-cli-runtime --features fault-hooks
if ($LASTEXITCODE -ne 0) { exit 1 }
$env:CARGO_TARGET_DIR = $target
cargo build --locked --release --manifest-path $manifest --bin navide-cli-runtime
if ($LASTEXITCODE -ne 0) { exit 1 }

$binary = Join-Path $target 'release\navide-cli-runtime.exe'
$harness = Join-Path $harnessTarget 'release\navide-cli-runtime.exe'
if (-not (Test-Path $binary)) { Write-Host 'sidecar was not built'; exit 1 }
if (-not (Test-Path $harness)) { Write-Host 'harness sidecar was not built'; exit 1 }

# The fault-hooks literal "fault: exit watcher could not start" exists only inside
# #[cfg(feature = "fault-hooks")] code: present in the harness binary (the positive control), absent from the release one.
node tests/cli/g2Native.mjs proof --release $binary --harness $harness
if ($LASTEXITCODE -ne 0) { exit 1 }

$sha = (Get-FileHash -Algorithm SHA256 $binary).Hash.ToLower()
"NAVIDE_CLI_RUNTIME_BIN=$binary" >> $env:GITHUB_ENV
"NAVIDE_T05_SIDECAR=$binary" >> $env:GITHUB_ENV
"G2_SIDECAR_SHA=$sha" >> $env:GITHUB_ENV
"G2_BASETEMP=$(Join-Path $env:RUNNER_TEMP 'g2-basetemp')" >> $env:GITHUB_ENV
"CARGO_TARGET_DIR=$target" >> $env:GITHUB_ENV
Write-Host "arch: $env:PROCESSOR_ARCHITECTURE; principal sidecar sha256: $sha"

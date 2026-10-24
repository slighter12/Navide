# G2 ticket 07: build the Windows sidecar the hosted run uses and export its path and hash.
# Release-shaped (no test features); the Windows harness has no fault-hooks tests.
$ErrorActionPreference = 'Stop'
$target = Join-Path $env:RUNNER_TEMP 'g2-cargo-target'
$env:CARGO_TARGET_DIR = $target
cargo build --locked --release --manifest-path native/navide-cli-runtime/Cargo.toml --bin navide-cli-runtime
if ($LASTEXITCODE -ne 0) { exit 1 }
$binary = Join-Path $target 'release\navide-cli-runtime.exe'
if (-not (Test-Path $binary)) { Write-Host 'sidecar was not built'; exit 1 }
$sha = (Get-FileHash -Algorithm SHA256 $binary).Hash.ToLower()
"NAVIDE_CLI_RUNTIME_BIN=$binary" >> $env:GITHUB_ENV
"NAVIDE_T05_SIDECAR=$binary" >> $env:GITHUB_ENV
"G2_SIDECAR_SHA=$sha" >> $env:GITHUB_ENV
"G2_BASETEMP=$(Join-Path $env:RUNNER_TEMP 'g2-basetemp')" >> $env:GITHUB_ENV
"CARGO_TARGET_DIR=$target" >> $env:GITHUB_ENV
Write-Host "arch: $env:PROCESSOR_ARCHITECTURE; principal sidecar sha256: $sha"

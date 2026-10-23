param([Parameter(Mandatory = $true)][string]$Case)

# Runs one sidecar_windows case by exact name; fails when it did not pass or was not collected.
$output = cargo test --locked --manifest-path native/navide-cli-runtime/Cargo.toml --test sidecar_windows $Case -- --exact --nocapture 2>&1 | Out-String
$status = $LASTEXITCODE
Write-Host $output
if ($status -ne 0) { exit 1 }
if ($output -notmatch 'test result: ok\. 1 passed') {
  Write-Host "case not collected: $Case"
  exit 1
}

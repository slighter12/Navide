param(
  [Parameter(Mandatory = $true)][string]$File,
  [Parameter(Mandatory = $true)][int]$Passed
)

# Runs a pytest file, or one `file::case` node, through the backend environment. Fails unless exactly $Passed
# cases passed and none was skipped, failed or left uncollected.
$output = uv --project backend run pytest $File -q -p no:cacheprovider 2>&1 | Out-String
$status = $LASTEXITCODE
Write-Host $output
if ($status -ne 0) { exit 1 }
if ($output -match 'skipped') {
  Write-Host "a case was skipped: $File"
  exit 1
}
if ($output -notmatch "(?m)\b$Passed passed\b") {
  Write-Host "expected $Passed passed: $File"
  exit 1
}

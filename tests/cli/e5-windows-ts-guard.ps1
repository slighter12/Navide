param(
  [Parameter(Mandatory = $true)][string]$Results,
  [Parameter(Mandatory = $true)][string]$Names
)

# Fails unless every case in $Names ('|' separated exact full names) appears exactly once in the vitest JSON
# report $Results and passed. A case that was not collected, was skipped or failed fails the guard.
$report = Get-Content $Results -Raw | ConvertFrom-Json
$all = @($report.testResults.assertionResults)
$bad = 0
foreach ($required in ($Names -split '\|')) {
  $found = @($all | Where-Object { $_.fullName -eq $required })
  if ($found.Count -ne 1) {
    Write-Host "collected $($found.Count) times, expected once: $required"
    $bad++
  } elseif ($found[0].status -ne 'passed') {
    Write-Host "status $($found[0].status): $required"
    $bad++
  }
}
if ($bad -ne 0) { exit 1 }

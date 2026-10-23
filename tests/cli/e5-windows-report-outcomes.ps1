# Surfaces every step outcome as a workflow annotation, so a failed run names its failing step even when the
# job log cannot be fetched. Reads the `steps` context as JSON from STEPS_JSON. Always exits 0.
$steps = $env:STEPS_JSON | ConvertFrom-Json
$listed = @()
foreach ($step in $steps.PSObject.Properties) {
  $outcome = $step.Value.outcome
  $listed += "$($step.Name)=$outcome"
  if ($outcome -ne 'success' -and $outcome -ne 'skipped') {
    Write-Host "::error title=E5 step failed::$($step.Name) outcome=$outcome"
  }
}
Write-Host "::notice title=E5 outcomes::$($listed -join ' ')"
exit 0

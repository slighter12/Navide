# G2 ticket 07: the required Windows backend case files against the freshly built sidecar.
# Files are listed explicitly; the collection guard then requires every id in
# tests/cli/g2-required-windows.txt to have passed.
$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force test-results/ci | Out-Null
uv --project backend run pytest `
  backend/tests/cli_regression/test_launch_rust_windows.py `
  backend/tests/test_cli_runtime_windows.py `
  backend/tests/test_cli_runtime_windows_close.py `
  -q -p no:cacheprovider -o junit_family=xunit2 `
  --basetemp $env:G2_BASETEMP `
  --junitxml test-results/ci/g2-windows-junit.xml
exit $LASTEXITCODE

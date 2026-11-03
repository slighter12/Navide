# G2 ticket 07: the required Windows backend case files against the freshly built sidecar.
# Files are listed explicitly; the collection guard then requires every id in
# tests/cli/g2-required-windows.txt to have passed.
# The three --deselect cases below are POSIX-only by design (skipif on Windows); deselecting them keeps a
# skipped case out of the JUnit, which the collection guard rejects unless its file is in --skip-allowed.
$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force test-results/ci | Out-Null
uv --project backend run pytest `
  backend/tests/cli_regression/test_launch_rust_windows.py `
  backend/tests/test_cli_runtime_windows.py `
  backend/tests/test_cli_runtime_windows_close.py `
  backend/tests/test_cli_launch_windows_words.py `
  backend/tests/test_cli_runtime_observe_windows.py `
  backend/tests/test_cli_runtime_observer_selection.py `
  backend/tests/test_main_graceful_shutdown.py `
  backend/tests/test_main_parent_watch.py `
  backend/tests/test_main_port_file_before_listen.py `
  backend/tests/test_main_shutdown_diag.py `
  backend/tests/test_main_stdin_shutdown.py `
  --deselect tests/test_main_graceful_shutdown.py::test_a_parked_rewake_hook_does_not_hold_a_sigtermed_backend_open `
  --deselect tests/test_main_shutdown_diag.py::test_the_harness_variable_registers_an_all_threads_dump `
  --deselect tests/test_main_shutdown_diag.py::test_a_hung_backend_writes_its_stacks_before_it_is_killed `
  -q -p no:cacheprovider -o junit_family=xunit2 `
  --basetemp $env:G2_BASETEMP `
  --junitxml test-results/ci/g2-windows-junit.xml
exit $LASTEXITCODE

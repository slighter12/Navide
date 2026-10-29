"""Phase 2 entry file: `uv --project backend run python backend/tests/cli_regression/support/g3/run_driver.py <command> ...` (commands: see `driver.main`). Puts `backend/` on the path, then runs it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tests.cli_regression.support.g3 import driver  # noqa: E402

raise SystemExit(driver.main(sys.argv[1:]))

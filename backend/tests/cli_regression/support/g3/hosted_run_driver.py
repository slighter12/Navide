"""Entry file of the hosted driver: `uv --project backend run python backend/tests/cli_regression/support/g3/hosted_run_driver.py <command> ...` (commands: see `hosted_driver`). Puts `backend/` on the path, then runs it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tests.cli_regression.support.g3 import hosted_driver  # noqa: E402

raise SystemExit(hosted_driver.main(sys.argv[1:]))

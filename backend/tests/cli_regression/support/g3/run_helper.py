"""Phase 2 entry file for the helper commands: `uv --project backend run python backend/tests/cli_regression/support/g3/run_helper.py <command> ...` (commands: see `helper`). Puts `backend/` on the path, then runs it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tests.cli_regression.support.g3 import helper  # noqa: E402

raise SystemExit(helper.main(sys.argv[1:]))

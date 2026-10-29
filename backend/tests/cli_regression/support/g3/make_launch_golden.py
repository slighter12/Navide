"""Writes `tests/cli/g3-launch-plan-golden.json`: the Python agent-launch plan for one fixed seed. The TypeScript plan (`agentLaunchPlan`) and the Python plan (`launch.plan`) are both tested
against this one file, which is how their equality is proven. Run: uv --project backend run python backend/tests/cli_regression/support/g3/make_launch_golden.py OUT_PATH"""
import json
import sys

from tests.cli_regression.support.g3 import launch

SEED = "00112233445566778899aabbccddeeff"

if __name__ == "__main__":
    body = {"seed": SEED, "plan": launch.plan(SEED), "calibrationPlan": launch.calibration_plan(SEED)}
    with open(sys.argv[1], "w", encoding="utf-8") as handle:
        handle.write(json.dumps(body, indent=1, sort_keys=True) + "\n")

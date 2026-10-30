from __future__ import annotations

import argparse
from pathlib import Path

from template_core.models import CanonicalPlan

from .geometry import execute_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one immutable canonical CAD plan")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--result", required=True)
    arguments = parser.parse_args()

    plan = CanonicalPlan.model_validate_json(Path(arguments.plan).read_text(encoding="utf-8"))
    result = execute_plan(plan, Path(arguments.output))
    Path(arguments.result).write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return 0 if result.success else 2


if __name__ == "__main__":
    raise SystemExit(main())

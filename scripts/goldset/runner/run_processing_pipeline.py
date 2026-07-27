from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from plan_processing_pipeline import PROCESSING_STEPS


@dataclass
class StepResult:
    order: int
    name: str
    command: list[str]
    returncode: int
    started_at: str
    finished_at: str
    stdout_log: str
    stderr_log: str


def find_goldset_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "processing").exists() and (parent / "runner").exists():
            return parent
    raise RuntimeError(f"Could not locate scripts/goldset root from {current}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run selected goldset processing scripts with per-step logs.")
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--include-cleanup", action="store_true", help="Include destructive cleanup steps.")
    parser.add_argument("--dry-run", action="store_true", help="Write the command manifest without executing subprocesses.")
    parser.add_argument("--stop-on-failure", action="store_true", default=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    goldset_root = find_goldset_root()
    runner_root = goldset_root / "runner"
    log_dir = runner_root / "logs" / args.run_id
    log_dir.mkdir(parents=True, exist_ok=True)

    results: list[StepResult] = []
    for order, step in enumerate(PROCESSING_STEPS, start=1):
        enabled = step.default_enabled or (args.include_cleanup and step.destructive)
        if not enabled:
            continue

        script_path = goldset_root / step.script
        command = [sys.executable, str(script_path)]
        stdout_path = log_dir / f"{order:02d}_{step.name}.stdout.log"
        stderr_path = log_dir / f"{order:02d}_{step.name}.stderr.log"
        started_at = datetime.now(timezone.utc).isoformat()

        if args.dry_run:
            returncode = 0
            stdout_path.write_text("DRY RUN: command not executed\n", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
        else:
            completed = subprocess.run(command, cwd=goldset_root, capture_output=True, text=True, check=False)
            returncode = completed.returncode
            stdout_path.write_text(completed.stdout, encoding="utf-8")
            stderr_path.write_text(completed.stderr, encoding="utf-8")

        result = StepResult(
            order=order,
            name=step.name,
            command=command,
            returncode=returncode,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
        )
        results.append(result)

        if returncode != 0 and args.stop_on_failure:
            break

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "goldset_root": str(goldset_root),
        "dry_run": args.dry_run,
        "include_cleanup": args.include_cleanup,
        "results": [asdict(result) for result in results],
    }
    manifest_path = log_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "steps": len(results)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

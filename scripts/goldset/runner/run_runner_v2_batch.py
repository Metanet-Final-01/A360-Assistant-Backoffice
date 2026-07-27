from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "runner" / "runner_v2.py"
CONVERTER = ROOT / "processing" / "convert_backend_recommendation.py"


def default_run_prefix() -> str:
    return f"runner_v2_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def run_command(args: list[str], cwd: Path, *, timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=args,
            returncode=124,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace"),
            stderr=((exc.stderr or "") if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace"))
            + f"\nTimed out after {timeout} seconds.\n",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run runner_v2 over all eval input PDFs and convert every result.")
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / "eval_inputs" / "pdfs")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--agent-version", default="v2")
    parser.add_argument("--run-prefix", default=default_run_prefix())
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start-at", default="")
    parser.add_argument("--no-convert", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdfs = sorted(args.pdf_dir.glob("*.pdf"))
    if args.start_at:
        pdfs = [path for path in pdfs if path.name >= args.start_at]
    if args.limit is not None:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        raise SystemExit(f"No PDFs found: {args.pdf_dir}")

    batch_dir = ROOT / "runner" / "logs" / args.run_prefix
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch_manifest = {
        "run_prefix": args.run_prefix,
        "base_url": args.base_url,
        "agent_version": args.agent_version,
        "pdf_dir": str(args.pdf_dir),
        "pdf_count": len(pdfs),
        "runs": [],
    }

    for index, pdf in enumerate(pdfs, start=1):
        case_slug = pdf.stem.split("__", 1)[0]
        run_id = f"{args.run_prefix}__{index:02d}_{case_slug}"
        manifest_path = ROOT / "runner" / "logs" / run_id / "run_manifest.json"
        runner_cmd = [
            sys.executable,
            str(RUNNER),
            "--input",
            str(pdf),
            "--base-url",
            args.base_url,
            "--agent-version",
            args.agent_version,
            "--run-id",
            run_id,
            "--timeout",
            str(args.timeout),
        ]
        print(f"[{index}/{len(pdfs)}] runner {pdf.name}", flush=True)
        runner_result = run_command(runner_cmd, ROOT, timeout=args.timeout + 60)
        (batch_dir / f"{run_id}.runner.stdout.log").write_text(runner_result.stdout, encoding="utf-8")
        (batch_dir / f"{run_id}.runner.stderr.log").write_text(runner_result.stderr, encoding="utf-8")

        record = {
            "index": index,
            "pdf": str(pdf),
            "run_id": run_id,
            "manifest": str(manifest_path),
            "runner_returncode": runner_result.returncode,
            "runner_status": "ok" if runner_result.returncode == 0 else "failed",
            "converter_returncode": None,
            "converter_status": "skipped" if args.no_convert else None,
        }

        if runner_result.returncode == 0 and not args.no_convert:
            converter_cmd = [
                sys.executable,
                str(CONVERTER),
                str(manifest_path),
                "--with-conversions",
            ]
            print(f"[{index}/{len(pdfs)}] convert {run_id}", flush=True)
            converter_result = run_command(converter_cmd, ROOT, timeout=300)
            (batch_dir / f"{run_id}.converter.stdout.log").write_text(converter_result.stdout, encoding="utf-8")
            (batch_dir / f"{run_id}.converter.stderr.log").write_text(converter_result.stderr, encoding="utf-8")
            record["converter_returncode"] = converter_result.returncode
            record["converter_status"] = "ok" if converter_result.returncode == 0 else "failed"

        batch_manifest["runs"].append(record)
        (batch_dir / "batch_manifest.json").write_text(json.dumps(batch_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        failed = record["runner_status"] != "ok" or record.get("converter_status") == "failed"
        if failed and not args.continue_on_error:
            print(json.dumps(record, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

    print(json.dumps(batch_manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

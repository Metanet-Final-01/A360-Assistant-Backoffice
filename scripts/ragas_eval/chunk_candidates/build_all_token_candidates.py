"""토큰수 기반 청킹 후보 11개(overlap=0)를 순차로 빌드한다.
교수님 지시로 토큰 기반 청킹도 꼭 테스트 — 글자수 기반(300/600/900/1200/1500)과 별개로
토큰 기준 후보군(150/300/600/900/1200/1500 + 128/256/512/1024/2048)을 미리 만들어둔다.
"""
import subprocess
import sys
import time
from pathlib import Path

PY = sys.executable
SCRIPT = Path(__file__).parent / "build_candidate_token.py"

TOKEN_SIZES = [128, 150, 256, 300, 512, 600, 900, 1024, 1200, 1500, 2048]

for ts in TOKEN_SIZES:
    print(f"[{time.strftime('%H:%M:%S')}] === token_size={ts} ov=0 시작 ===", flush=True)
    proc = subprocess.Popen(
        [PY, str(SCRIPT), "--token-size", str(ts), "--overlap-tokens", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    for line in proc.stdout:
        print(line, end="", flush=True)
    returncode = proc.wait()
    if returncode != 0:
        print(f"[{time.strftime('%H:%M:%S')}] token_size={ts} 실패(returncode={returncode}) — 중단", flush=True)
        break
    print(f"[{time.strftime('%H:%M:%S')}] === token_size={ts} 완료 ===", flush=True)

print("=== 전체 종료 ===", flush=True)

# ops-server 통합 실행 — 백엔드(FastAPI :8100)와 프론트(Streamlit :8501)를 함께 띄운다.
#
# 사용법:
#   cd ops-server
#   python -m venv .venv ; .\.venv\Scripts\Activate.ps1   # (최초 1회) 가상환경
#   pip install -r requirements.txt
#   copy backend\.env.example backend\.env                # 값 채우기
#   .\start.ps1
#
# 프론트(Streamlit)가 포그라운드로 돌고, 종료(Ctrl+C)하면 백엔드도 함께 정리된다.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "▶ 백엔드(FastAPI) 기동: http://localhost:8100" -ForegroundColor Cyan
$backend = Start-Process -PassThru -WorkingDirectory (Join-Path $root "backend") `
    -FilePath "python" -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8100"

try {
    Write-Host "▶ 프론트(Streamlit) 기동: http://localhost:8501" -ForegroundColor Cyan
    Push-Location (Join-Path $root "frontend")
    python -m streamlit run app.py --server.port 8501
}
finally {
    Pop-Location
    Write-Host "■ 백엔드 종료 중..." -ForegroundColor Yellow
    if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
}

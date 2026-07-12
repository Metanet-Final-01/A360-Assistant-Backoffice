<!-- PR 제목: type(scope): 한글 제목 (RPA-키) -->

## 관련 이슈

<!-- Jira: RPA-120 / GitHub 이슈: #12 -->

- Jira:

## 무엇을 왜 변경했나요?

<!-- 리뷰어가 diff를 열기 전에 맥락을 이해할 수 있게 2~5줄로 -->

## 주요 변경 사항

-

## 확인 방법

<!-- 리뷰어가 로컬에서 확인하는 절차 (서버 기동 후 확인 경로 등) -->

```powershell
# rag-server:        cd rag-server ; uvicorn app.main:app --port 8200
# monitoring-server: cd monitoring-server ; .\start.ps1   # :8100 + :8501
```

## 스크린샷 (해당 시)

<!-- Streamlit 화면 변경은 전/후 스크린샷을 첨부해주세요 -->

## 체크리스트

- [ ] 스스로 diff를 리뷰했습니다 (print 디버그/임시 코드/불필요한 주석 제거)
- [ ] 시크릿(관리자 계정·DB URL·토큰)·개인정보가 포함되지 않았습니다 (.env 커밋 금지)
- [ ] 관련 문서(.env.example, README, docs/)를 갱신했습니다 (해당 시)
- [ ] Streamlit 화면 변경 시 스크린샷을 첨부했습니다 (해당 시)

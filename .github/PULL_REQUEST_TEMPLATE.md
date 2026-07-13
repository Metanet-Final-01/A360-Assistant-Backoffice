<!-- PR 제목: type(scope): 한글 제목 (RPA-키) -->

## 관련 이슈

<!-- Jira: RPA-120 / GitHub 미러 이슈: Closes #12 -->

- Jira:
- GitHub Issue: Closes #

<!-- GitHub 미러 이슈가 아직 없거나 Closes로 닫지 않는 경우, 이유를 적어주세요. -->

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

- [ ] PR 제목에 Jira 키가 포함되어 있습니다. 예: `feat(eval): 평가 화면 개선 (RPA-120)`
- [ ] GitHub 미러 이슈를 `Closes #번호`로 연결했습니다.
- [ ] 스스로 diff를 리뷰했습니다 (print 디버그/임시 코드/불필요한 주석 제거)
- [ ] 시크릿(관리자 계정·DB URL·토큰)·개인정보가 포함되지 않았습니다 (.env 커밋 금지)
- [ ] 관련 문서(.env.example, README, docs/)를 갱신했습니다 (해당 시)
- [ ] 테스트를 추가/갱신했거나, 생략 사유를 확인 방법에 적었습니다 (해당 시)
- [ ] Streamlit 화면 변경 시 스크린샷을 첨부했습니다 (해당 시)

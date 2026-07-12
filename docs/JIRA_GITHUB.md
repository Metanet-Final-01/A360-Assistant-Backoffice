# Jira ↔ GitHub 연동 가이드 (백오피스)

> Jira 사이트: `metanetfinal.atlassian.net` / 프로젝트 키: **RPA** (백엔드·프론트와 같은 프로젝트 사용)
> 연동 원리·전체 설정 절차의 원본 기록은 **백엔드 리포 `docs/JIRA_GITHUB.md`** 다.
> 이 문서는 백오피스 리포 관점에서 "무엇이 그대로 되고, 무엇이 추가로 필요한지"만 기록한다.

## 설정 없이 이미 동작하는 것

GitHub for Jira 앱이 `Metanet-Final-01` **조직 단위**로 연결되어 있어서, 이 리포에서도:

1. 브랜치명/커밋 메시지/PR 제목에 `RPA-N`이 있으면 → Jira 이슈의 **개발 패널**에 자동 링크
2. 스마트 커밋 (`#comment`, `#time`, `#done`) 사용 가능
3. **규칙 A**: 브랜치 생성 → Jira 이슈 In Progress 자동 전환
4. **규칙 B**: PR 머지 → Jira 이슈 Done 자동 전환

규칙 A/B는 Jira 프로젝트 레벨 Automation이라 리포를 구분하지 않는다 — 추가 설정 불필요.
(프론트 리포 연결 때 조직 단위 연결이 신규 리포까지 커버함을 실제 동작으로 확인했다.)

## 추가 설정이 필요한 것 — GitHub 미러 이슈 (규칙 C/D)

규칙 C(Jira 이슈 생성 → GitHub 이슈 생성)와 규칙 D(Jira Done → GitHub 이슈 닫기)는
웹 요청 URL에 리포가 하드코딩되어 있고, **미러를 어느 리포에 만들지 라벨로 구분**한다.
프론트 연결 때 If(라벨에 `frontend` 없음 → 백엔드)/Else(→ 프론트) 2분기가 이미 있으므로,
백오피스는 **3분기로 확장**한다.

### 라우팅 규칙 (확장 후)

| Jira 이슈 라벨 | 미러 생성/닫기 대상 리포 |
|---|---|
| `backoffice` | A360-Assistant-Backoffice (이 리포) |
| `frontend` | A360-Assistant-Frontend |
| (없음) | A360-Assistant-Backend |

### 1. GitHub 토큰 확인

- **Classic PAT (`repo` scope) 사용 중**: 토큰 자체는 계정이 접근 가능한 모든 리포에 적용되므로 수정 불필요.
  토큰 소유 계정이 `A360-Assistant-Backoffice`에 쓰기 접근 권한이 있는지만 확인한다.
- Fine-grained PAT로 교체하는 경우에만 Repository access에 이 리포를 추가한다 (Issues → Read and write).

### 2. 규칙 C — 기존 If/Else를 3분기로 확장

현재 구조: If(`레이블`에 `frontend` 없음) → 백엔드 웹 요청 / Else → 프론트 웹 요청.

1. **If 조건 수정**: 값에 `backoffice`를 추가 —
   - 필드: `레이블`, 조건: "다음 중 어느 것도 포함하지 않음", 값: `frontend`, `backoffice`
   - (참 경로의 기존 백엔드 액션들은 그대로)
2. **Else-if 분기 추가** (If 카드의 분기 추가 버튼):
   - 조건: 필드 `레이블`, "다음 중 하나 이상 포함", 값 `backoffice`
   - 안에 기존 웹 요청 카드를 **복제**해서 넣고 URL만 변경:
     `https://api.github.com/repos/Metanet-Final-01/A360-Assistant-Backoffice/issues`
     (Method/Headers/Body/"Delay execution..." 체크는 기존 것과 동일)
   - `업무 항목 필드 편집`(`GitHub Issue Number` = `{{webResponse.body.number}}`)도 복제해서 웹 요청 뒤에 배치
3. 마지막 Else(프론트)는 그대로 둔다.

> 분기 평가는 위에서 아래 순서다: 라벨 없음 → 백엔드, `backoffice` → 백오피스, 그 외(`frontend`) → 프론트.

### 3. 규칙 D — 동일한 3분기 패턴

- Trigger(`Done으로 전환`)와 `GitHub Issue Number` 비어있지 않음 조건은 그대로
- If 조건 값에 `backoffice` 추가 (백엔드 PATCH가 참 경로에 남음)
- **Else-if(레이블 포함 `backoffice`) 추가** → PATCH 웹 요청 복제 후 URL 변경:
  `https://api.github.com/repos/Metanet-Final-01/A360-Assistant-Backoffice/issues/{{issue.GitHub Issue Number}}`

## 운영 흐름 요약 (백오피스 작업)

```
Jira 이슈 생성 (RPA-120, 라벨: backoffice)
  └→ [규칙 C·backoffice 분기] 이 리포에 GitHub 이슈 #N 자동 생성
브랜치 생성: feat/RPA-120-ingest-scheduler
  └→ [규칙 A] Jira: In Progress
커밋: "feat(sched): 적재 주기 트리거 구현 (RPA-120)"
  └→ Jira 개발 패널에 커밋 링크
PR 생성 (제목에 RPA-120, 본문에 Closes #N, base: dev)
  └→ Jira 개발 패널에 PR 링크
PR 머지
  ├→ [규칙 B] Jira: Done
  ├→ [규칙 D·backoffice 분기] GitHub 이슈 #N 닫힘
  └→ GitHub 네이티브: Closes #N로도 닫힘
```

## 트러블슈팅

- **미러 이슈가 백엔드 리포에 생겼다**: Jira 이슈에 `backoffice` 라벨을 빼먹었거나, 규칙 C의 3분기 확장 전에 만든 이슈. 라벨을 추가해도 이미 생성된 미러는 옮겨지지 않으므로, 잘못 생긴 미러를 수동으로 닫고 필요 시 이 리포에 다시 만든다
- **웹 요청 401/403**: 토큰에 이 리포 권한이 없는 경우. Automation → Audit log에서 응답 코드 확인
- **개발 패널에 안 뜸**: Jira 키 대소문자 확인 (`RPA-120`, 소문자는 인식 안 될 수 있음)
- 그 외 공통 이슈는 백엔드 리포 `docs/JIRA_GITHUB.md` 트러블슈팅 참고

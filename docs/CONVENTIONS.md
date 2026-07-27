# 협업 컨벤션

> 심사위원이 리포지토리를 직접 확인하는 것을 전제로, 히스토리·PR·이슈가 "읽히는 기록"이 되도록 관리한다.
> 백엔드(A360-Assistant-Backend)·프론트(A360-Assistant-Frontend) 리포와 공통 규칙이며,
> scope·담당 영역만 백오피스에 맞게 다르다.

## 1. 브랜치 전략

**main + dev 2단 구조**를 사용한다.

```
main ← 릴리스 브랜치. dev에서 검증된 것만 머지 (직접 push 금지)
 └── dev ← 통합 브랜치. 모든 작업 PR의 대상 (직접 push 금지)
      └── feat/RPA-120-ingest-scheduler ← 작업 브랜치 (dev에서 분기)
```

- 작업 브랜치는 **dev에서 분기**하고, PR도 **dev로** 보낸다.
- `dev → main` 머지는 릴리스 시점에 담당자와 협의하여 진행한다.

### 브랜치 네이밍

```
<type>/<Jira키>-<영문-요약>
```

| 예시 | 용도 |
|---|---|
| `feat/RPA-120-ingest-scheduler` | 기능 개발 |
| `fix/RPA-131-eval-xlsx-encoding` | 버그 수정 |
| `refactor/RPA-140-split-collectors` | 리팩터링 |
| `docs/RPA-108-conventions` | 문서 |
| `chore/RPA-108-repo-conventions-setup` | 설정/잡무 |

- Jira 키가 브랜치명에 있으면 Jira 개발 패널에 자동 연결된다 (GitHub for Jira 앱 설치 시).
- 브랜치는 머지 후에도 삭제하지 않고 남겨둔다 (진행 이력 보존).

## 2. 커밋 컨벤션

[Conventional Commits](https://www.conventionalcommits.org/) 형식 + **한글 제목**.

```
<type>(<scope>): <한글 제목> (<Jira키>)

<본문: 무엇을이 아니라 "왜"를 쓴다. 선택>
```

예시:

```
feat(obs): 수집 실패 시 마지막 성공 시각 표시 (RPA-125)

수집이 조용히 실패하면 대시보드가 언제 데이터인지 알 수 없어서,
소스별 last_collected_at과 에러를 화면에 노출한다.
```

### type

| type | 용도 |
|---|---|
| `feat` | 기능 추가 |
| `fix` | 버그 수정 |
| `refactor` | 동작 변경 없는 구조 개선 |
| `docs` | 문서만 변경 |
| `test` | 테스트 추가/수정 |
| `chore` | 빌드/설정/잡무 |
| `ci` | CI/CD 파이프라인 |
| `perf` | 성능 개선 |
| `style` | 포맷팅 (동작 무관) |

### scope (선택, 서버·모듈 기준)

| scope | 영역 |
|---|---|
| `rag` | rag-server (적재 파이프라인·API) |
| `obs` | 관측 — 조회 API(main.py) + 수집기·백엔드 클라이언트(observability/) 전부 |
| `eval` | 평가 모듈 (데이터셋·채점·변환·A/B, RAGAS 포함) |
| `load` | 부하테스트 (k6 결과 수집·이력) |
| `ui` | Streamlit 화면 |
| `sched` | 스케줄러 |
| `build` `ci` | 빌드·CI |

`mon`(조회 API)과 `obs`(수집기)는 원래 나눠져 있었는데, 실제로는 거의 항상 같이
바뀌어서(엔드포인트 하나 추가하면 라우트+수집기 둘 다 손댐) 나눌 이유가 없어
`obs`로 합쳤다.

### 규칙

- 제목 50자 이내, 마침표 없이
- 1 커밋 = 1 논리적 변경 (수집기 수정과 화면 변경을 한 커밋에 넣지 않기)
- Jira 키를 커밋 메시지에 포함하면 Jira에 자동 링크 + 스마트 커밋 사용 가능
  - 스마트 커밋 예: `fix(eval): xlsx 한글 깨짐 보정 (RPA-131) #comment openpyxl 인코딩 지정 #done`

## 3. PR 컨벤션

- **PR 제목은 커밋 컨벤션과 동일한 형식**으로 쓴다.
- 머지는 **Merge commit** 방식을 사용한다 — 작업 브랜치의 개별 커밋이 dev 히스토리에 그대로 보존된다. 따라서 **개별 커밋 메시지도 전부 컨벤션을 지켜야 한다** (`wip`, `수정` 같은 커밋을 남기지 않기).
- 본문은 PR 템플릿(`.github/PULL_REQUEST_TEMPLATE.md`)을 따른다. **Streamlit 화면 변경은 전/후 스크린샷 첨부.**
- **리뷰어 최소 1인 승인** 후 머지. 본인 승인으로 본인 PR 머지 금지.
- 리뷰 요청 전 스스로 diff를 한 번 훑는다 (print 디버그, 임시 코드, .env 잔재 제거).
- 작업 중 공유가 필요하면 **Draft PR**로 올린다.
- PR 크기는 리뷰 가능한 수준으로 유지 (대략 ±500줄 이내 권장, 넘으면 분할 고려).

## 4. 이슈·라벨

- 이슈 트래킹의 원본(source of truth)은 **Jira** (백엔드·프론트와 같은 `RPA` 프로젝트). GitHub 이슈는 Jira Automation으로 동기화된다 (`docs/JIRA_GITHUB.md` 참고).
- **백오피스 작업의 Jira 이슈에는 라벨 `backoffice`를 붙인다** — Automation이 이 라벨로 미러 이슈를 이 리포에 생성한다.
- 라벨 세트:

| 라벨 | 용도 |
|---|---|
| `area:rag` `area:monitoring` `area:eval` | 담당 영역 |
| `priority:P0` `priority:P1` `priority:P2` | 우선순위 (P0=필수 연계, P1=가점 효율, P2=차별화) |
| `type:feat` `type:bug` `type:docs` | 성격 |
| `from-jira` | Jira Automation이 생성한 이슈 |

## 5. 담당 영역

| 영역 | 폴더 | 담당 |
|---|---|---|
| RAG 적재 파이프라인·API | `rag-server/` | RAG 담당 |
| 모니터링·평가 (백엔드+화면) | `ops-server/` | 모니터링·평가 담당 |
| 컨벤션·문서 | `.github/`, `docs/` | 공용 |

실서비스 백엔드(A360-Assistant-Backend)의 API 스키마·관측 DB 스키마가 필요하면
임의로 가정하지 말고 백엔드 리포 문서를 확인하거나 백엔드 담당에게 합의를 요청한다.

## 6. 금지 사항

- `main`·`dev` 직접 push
- 시크릿(관리자 계정·DB URL·토큰) 커밋 — `.env`는 절대 커밋하지 않고 `.env.example`만 갱신
- `--force` push (본인 작업 브랜치에서 rebase 후는 `--force-with-lease` 허용)
- 리뷰 없는 머지

## 7. 작업 흐름 — 트랙 A(AI 자동) / 트랙 B(수동)

팀원마다 Claude Code 사용 여부가 다르므로 두 트랙을 모두 지원한다.
**결과물 규칙(브랜치명·커밋·PR·리뷰)과 Jira Automation(미러 생성·상태 전환)은 두 트랙에서 완전히 동일하다.**

### 트랙 A: Claude Code 사용 (Atlassian MCP 연동)

1. Claude에게 자연어로 작업을 요청한다 (예: "수집 상태 화면에 에러 표시 추가해줘")
2. Claude가 Jira 이슈 생성(라벨 `backoffice` 포함) → dev에서 브랜치 분기 → 구현·커밋 → PR 생성까지 수행한다
3. 사람은 **PR 리뷰와 머지만** 담당한다 (승인 1인 규칙 동일)

최초 1회 설정: `claude mcp add --transport http atlassian https://mcp.atlassian.com/v1/mcp` 등록 후, 대화형 세션에서 `/mcp` → atlassian → 본인 Atlassian 계정으로 인증.

### 트랙 B: 수동 작업 (Claude Code 없이)

1. Jira에서 **"작업" 이슈 생성** (담당자 본인 지정, **라벨 `backoffice` 추가**)
2. 이슈 화면 오른쪽 **개발 패널 → "브랜치 만들기"** (또는 로컬: `git checkout dev && git pull && git checkout -b feat/RPA-N-영문요약`)
3. 작업·커밋 (2장 커밋 컨벤션 준수) → push
4. GitHub에서 PR 생성 — **base: `dev`**, 템플릿 작성, 본문에 `Closes #미러이슈번호` (미러 번호는 GitHub Issues에서 `[RPA-N]`으로 검색)
5. 리뷰 승인 후 **Create a merge commit**으로 머지

Jira 상태는 손대지 않아도 된다 — 브랜치 생성 시 "진행 중", PR 머지 시 "완료"로 자동 전환된다.

### 공통 주의

- **GitHub 이슈를 직접 만들지 않는다** — Jira에서 이슈를 만들면 Automation이 GitHub 미러(`[RPA-N] ...`, `from-jira` 라벨)를 자동 생성한다
- GitHub 미러 이슈를 직접 수정·닫지 않는다 (PR의 `Closes #`로 닫히는 것은 예외)

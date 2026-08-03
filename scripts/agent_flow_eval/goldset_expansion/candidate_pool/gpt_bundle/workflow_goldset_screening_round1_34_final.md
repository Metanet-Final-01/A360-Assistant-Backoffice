# Workflow Goldset Screening — 최종 분류

## 요약

| 분류 | 개수 | 의미 |
|---|---:|---|
| Main | 10 | 현재 액션·구조 중심 평가에 바로 사용 가능 |
| Challenge | 9 | 파라미터·외부 규칙·UI 속성 또는 전처리가 필요 |
| 제외 | 15 | 템플릿·로깅·설정 로더·패키지 데모 등 정답으로 강제할 가치가 낮음 |
| **합계** | **34** | |

## Main 후보

| ID | 후보 | 선정 이유 |
|---|---|---|
| 0011 | **Tool LogReviewData** | Excel 열기 → 마지막 행 이동 → 여러 결과 입력 → 저장·종료가 명확함 |
| 0017 | **GetOutcomes** | Excel 범위 읽기 → 문자열 정리 → List 구성의 처리 과정이 명확함 |
| 0033 | **Update Excel Status** | Excel 열기 → 특정 셀 상태 기록 → 닫기. 단순하지만 기능이 명확함 |
| 0089 | **Archival Of Files** | 폴더 확인 → 날짜 폴더 생성 → 파일 반복 → 복사 → 원본 삭제가 액션과 구조에 명확히 드러남 |
| 0098 | **Delete Junk Files And Folder** | 여러 임시 폴더를 반복 탐색해 파일·폴더를 삭제하는 과정이 명확함 |
| 0131 | **Currency Rate – Oanda** | Excel 입력 반복 → 웹 환율 조회 → 결과와 일자 기록. Loop와 로딩 대기 구조도 평가 가능 |
| 0140 | **New User Registration with Unique User ID** | 사용자 정보 입력 → ID 생성 → Excel 레코드 추가. 일반 액션으로 구성되고 결과 산출물이 분명함 |
| 0376 | **FileBackup** | Excel 설정 읽기 → 백업 폴더 생성 → 파일명 형태에 따라 분기 → 복사 → 원본 삭제. If 구조까지 평가 가능 |
| 0419 | **SendBulkEmailsWithTemplate** | Excel에서 수신자·템플릿 조회 → 반복 → 제목·본문 치환 → 이메일 발송 → 성공 여부 기록 |
| 0447 | **Sending Birthday Email** | 현재 날짜 계산 → Excel 직원 목록 반복 → 생일 일치 조건 확인 → 대상자 이메일 발송 |

## Challenge 후보

| ID | 후보 | Challenge 사유 | Main 복귀 조건 |
|---|---|---|---|
| 0080 | **Read Mail With Users Choices Connection Type** | 동일한 Email.emailConnect라도 실제 차이가 serverType=IMAP/OUTLOOK/EWS 파라미터에 있음 | serverType을 canonical action으로 정규화하면 Main 복귀 가능 |
| 0084 | **PDF Split By Blank Page** | PDF.splitDocument만으로 분할 방식이 드러나지 않고 splitDocumentOptions를 봐야 목적을 알 수 있음 | BLANK_PAGE_SEPARATOR 등 분할 모드를 canonical action으로 정규화 |
| 0232 | **FindNextWorkingDate** | 실제 근무일 계산 규칙이 외부 Excel 수식과 특정 셀에 숨겨져 있어 워크플로만으로 채점하기 어려움 | Excel 파일 또는 계산 규칙을 평가 입력에 포함 |
| 0239 | **Send Bonus Notification Email TaskBot** | 업무 흐름은 있으나 로그 폴더·오래된 로그 삭제·오류 기록·스크린샷 등 공통 보조 액션 비중이 큼 | 공통 보조 액션을 제거하고 핵심 흐름만 canonical workflow로 구성 |
| 0276 | **CheckIfInputisAlphaNumeric** | String.find의 의미가 정규식 파라미터에 의해 결정되어 액션명만으로 판별 유형을 구분하기 어려움 | 정규식 또는 판별 유형을 canonical condition으로 정규화 |
| 0288 | **GetQuarterInformation** | 대부분 String.assign이며 실제 분기 의미가 조건값과 할당 문자열에 들어 있음 | If 조건과 분기별 결과값까지 채점하는 파라미터 인식 평가 필요 |
| 0307 | **Payment Reminder with Cross Sell** | 전체 흐름은 좋지만 Cross Sell 결정이 외부 DMN 파일에 있고 Twilio 호출도 세부 입력에 의존 | DMN 규칙과 Twilio 주요 파라미터를 평가 입력에 포함 |
| 0313 | **SendEmailWithImageHTML** | 핵심 차이가 Email.sendMail 액션이 아니라 bodyFormat=HTML과 HTML message 내부에 있음 | 메일 형식과 본문 유형을 파라미터 인식 채점에 포함 |
| 0404 | **BreatheHR Expense Assistant** | 대부분 동일한 Recorder.capture이며 클릭·입력·체크 구분과 UI 대상이 하위 속성에 있음 | Recorder 하위 action type과 UI object를 canonical action으로 추출 |

## 제외

| ID | 후보 | 제외 이유 | 제외 유형 |
|---|---|---|---|
| 0048 | **Setup Log Folders and Locations** | 로그 경로·폴더 생성과 오래된 파일 정리 중심의 공통 초기화 유틸리티 | 공통 보조 유틸리티 |
| 0104 | **Template Taskbot** | 실제 업무가 아니라 경로·타임아웃·폴더 구조를 초기화하는 공통 템플릿 | 템플릿 |
| 0106 | **Execution and Error Logs** | 실행·오류 로그 기록만 수행하는 공통 운영 보조 워크플로 | 로깅 전용 |
| 0147 | **Salesforce Multi Language** | Salesforce 생성·조회·수정·삭제 기능을 순차 시연하는 패키지 데모 | 패키지 데모 |
| 0149 | **PrepareDateTime** | 감사 로그 조회를 위한 시간 문자열 전처리 유틸리티로 독립 업무 프로세스가 아님 | 공통 유틸리티 |
| 0161 | **ReadConfig** | XML 설정을 Dictionary로 읽는 공통 구성 로더. 특정 업무의 필수 구현으로 강제할 필요가 없음 | 설정 로더 |
| 0164 | **Read Configuration** | 구성 로더에 로그·폴더 초기화가 대량 결합된 공통 보조 워크플로 | 설정 로더·초기화 |
| 0199 | **Slack Integration Package** | 채널 생성·초대·게시·조회·답글을 한 번씩 실행하는 패키지 기능 데모 | 패키지 데모 |
| 0348 | **Microsoft Word Package Demo** | Word Replace·Bookmark·Paragraph·Create 기능을 한 번씩 보여주는 기능 데모 | 패키지 데모 |
| 0359 | **SubTask_ReadUserConfig** | Excel 설정을 Dictionary로 읽는 공통 설정 로더 | 설정 로더 |
| 0381 | **Salesforce Package Sample Bot** | 인증 후 Create·Read·Update·Delete를 차례로 실행하는 CRUD 데모 | 패키지 데모 |
| 0411 | **subTrazaMensajeBot** | 오류 메시지 조립·로그·스크린샷 중심의 공통 로깅 서브태스크 | 로깅 서브태스크 |
| 0412 | **Camunda DMN Package** | DMN 코드·파일 평가 기능을 시연하는 패키지 데모이며 실제 규칙은 DMN 본문에 존재 | 패키지 데모·외부 규칙 |
| 0425 | **Trello Package** | 팀·보드·멤버·리스트·카드·댓글·첨부·라벨 기능을 차례대로 실행하는 패키지 기능 시연 | 패키지 데모 |
| 0461 | **HTML Parser Package** | selector·태그·정규식 검색 기능을 각각 실행하는 사용 예제이며 모두 사용할 필연성이 없음 | 패키지 데모 |

## 분류 기준

### Main
- 액션명과 제어구조만으로 주요 업무 흐름을 식별할 수 있다.
- 특정 외부 구현이나 세부 파라미터를 정답으로 강제하지 않아도 된다.
- 단일 시스템이어도 If·Loop·읽기·처리·쓰기 구조가 명확하면 유지한다.

### Challenge
- 평가 가치는 있으나 동일 액션의 mode/type, 정규식, HTML 본문, UI 객체 또는 외부 Excel·DMN 규칙까지 봐야 한다.
- 공통 로깅·초기화 액션을 제거한 뒤 핵심 흐름만 평가해야 하는 경우도 포함한다.
- 평가기 확장 또는 전처리 규칙이 마련되면 Main으로 복귀할 수 있다.

### 제외
- 패키지 기능을 순서대로 보여주는 데모이다.
- 템플릿, 설정 로더, 로깅 전용, 공통 초기화 유틸리티이다.
- 특정 업무 과정에서 반드시 필요한 구현으로 보기 어렵다.

## 주의사항

- File, Folder, String, MessageBox 등 패키지 이름만으로 일괄 제외하지 않는다.
- 동일 패키지도 실제 업무 흐름이면 Main이 될 수 있고, 공통 유틸리티이면 제외될 수 있다.
- Challenge는 가치가 낮다는 의미가 아니라 현재 평가기의 범위를 넘어선다는 의미이다.

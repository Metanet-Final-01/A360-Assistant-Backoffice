"""action_label_ko — ko 문서 제목 → 한국어 액션 라벨 정제 (edit 이름 지목 리졸버용).

백엔드 label_candidates가 스펙 label을 사용자 문장에 부분 일치시키므로, 문서 관용
표기("<패키지>의 <라벨> 작업")를 벗겨 짧은 라벨을 만들어야 매칭이 성립한다.
"""

from app.rag.build.common import action_label_ko


def test_strips_package_prefix_and_action_suffix():
    assert action_label_ko("Google Drive", None, "Google Drive의 파일 이동 작업") == "파일 이동"
    assert action_label_ko("Clipboard", None, "Clipboard의 지우기 작업") == "지우기"


def test_strips_korean_package_prefix():
    assert action_label_ko("Error handler", "오류 처리기", "오류 처리기의 Try 작업") == "Try"


def test_strips_using_variant_suffix():
    # "Using the ~ action"의 ko형 "~ 작업 사용"도 라벨로 정제된다
    assert action_label_ko("Active Directory", None, "컴퓨터 이동 작업 사용") == "컴퓨터 이동"


def test_keeps_title_without_conventions():
    assert action_label_ko("REST Web Services", None, "삭제 방법") == "삭제 방법"
    assert action_label_ko("If", None, "Else 작업") == "Else"


def test_none_when_no_ko_pair():
    assert action_label_ko("X", None, None) is None

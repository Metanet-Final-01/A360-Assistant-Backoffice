import unittest
from unittest.mock import patch

from action_matching import (
    ScoredAction,
    canonicalize_action,
    classify_core_business_actions,
    flatten_scored_actions,
    load_action_equivalence_map,
    load_conditional_equivalence_groups,
    pair_judge_matches,
    score_branch_coverage,
)
from adapters.worfbench_adapter import _canonical_label, load_action_equivalence_map as load_adapter_equivalence_map


class ActionEquivalenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plain = load_action_equivalence_map()
        cls.conditional = load_conditional_equivalence_groups()

    def canonical(self, package: str, action: str) -> str:
        return canonicalize_action(package, action, {}, self.plain, self.conditional)

    def test_catalog_aliases_and_label_normalization(self) -> None:
        self.assertEqual(self.canonical("Gmail", "send"), "Email.cloudUsingSendAction")
        self.assertEqual(self.canonical("Locale", "datetostring"), "Datetime.cloudConvertDatetimeToString")
        self.assertEqual(
            self.canonical("Data Table", "dataTablePackageDeleteRowAction"),
            "DataTable.deleteRow",
        )
        self.assertEqual(
            self.canonical("Data Table", "dataTablePackageInsertRowAction"),
            "DataTable.insertRow",
        )

    def test_adapter_uses_same_label_normalization(self) -> None:
        mapping = load_adapter_equivalence_map()
        self.assertEqual(_canonical_label("Gmail", "send", mapping), "Email.cloudUsingSendAction")

    def test_unregistered_labels_ignore_case_and_spaces(self) -> None:
        self.assertEqual(
            self.canonical("Unlisted Package", "Do Thing"),
            self.canonical("unlistedpackage", "dothing"),
        )

    def test_excel_close_aliases_share_one_canonical_action(self) -> None:
        expected = self.canonical("Excel_MS", "CloseSpreadsheet")
        self.assertEqual(self.canonical("Excel", "CloseSpreadsheet"), expected)
        self.assertEqual(
            self.canonical(
                "Microsoft 365 Excel package in Automation 360",
                "office365ExcelClose",
            ),
            expected,
        )

    def test_excel_go_to_cell_aliases_share_one_canonical_action(self) -> None:
        expected = self.canonical("Excel_MS", "GoToCell")
        self.assertEqual(self.canonical("Excel", "GoToCell"), expected)
        self.assertEqual(
            self.canonical(
                "Microsoft 365 Excel package in Automation 360",
                "office365ExcelGoToCell",
            ),
            expected,
        )

    @patch("action_matching._embed", side_effect=RuntimeError("offline"))
    def test_judge_failure_returns_rule_only_diagnostic(self, _embed) -> None:
        gold = ScoredAction("g", "A", "one", "a.one")
        pred = ScoredAction("p", "B", "two", "b.two")

        matches, log = pair_judge_matches([gold], [pred])

        self.assertEqual(matches, [])
        self.assertEqual(log[0]["status"], "unavailable")
        self.assertEqual(log[0]["stage"], "embedding")


class BranchCoverageTest(unittest.TestCase):
    """실제 0376(FileBackup)의 if/elseIf/elseIf 3분기 구조(공통 copyFiles+
    deleteFiles가 경로만 다르게 반복)를 그대로 본떠서 검증한다 - 이 3분기는
    상호배타적인데 flatten_scored_actions의 반환값(out)에는 여전히 9개
    액션이 다 풀려서 하나의 리스트로 나와야 하고(기존 동작 안 바뀜),
    _branch_groups 사이드채널에만 분기별 소속이 별도로 기록돼야 한다."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.plain = load_action_equivalence_map()
        cls.conditional = load_conditional_equivalence_groups()

    def _three_way_if_steps(self) -> list[dict]:
        def branch_body(prefix: str) -> list[dict]:
            return [
                {"type": "action", "package": "MessageBox", "action": "messageBox", "uid": f"{prefix}-msg"},
                {"type": "action", "package": "File", "action": "copyFiles", "uid": f"{prefix}-copy"},
                {"type": "action", "package": "File", "action": "deleteFiles", "uid": f"{prefix}-delete"},
            ]

        return [
            {
                "type": "if",
                "uid": "outer-if",
                "steps": branch_body("if"),
                "branches": [
                    {"branch": "elseIf", "steps": branch_body("elif1")},
                    {"branch": "elseIf", "steps": branch_body("elif2")},
                ],
            }
        ]

    def test_flatten_pools_all_branches_but_records_branch_groups_separately(self) -> None:
        branch_groups: list[dict] = []
        out = flatten_scored_actions(
            self._three_way_if_steps(), plain_map=self.plain, conditional_groups=self.conditional, _branch_groups=branch_groups
        )

        # 기존 동작 그대로: 상호배타적 3분기가 여전히 하나의 flat 리스트(9개)로 풀린다.
        self.assertEqual(len(out), 9)

        self.assertEqual(len(branch_groups), 1)
        group = branch_groups[0]
        self.assertEqual([b["branch_name"] for b in group["branches"]], ["if", "elseIf", "elseIf"])
        for branch in group["branches"]:
            self.assertEqual(len(branch["uids"]), 3)

    def test_branch_score_gives_partial_credit_when_only_one_branch_implemented(self) -> None:
        branch_groups: list[dict] = []
        out = flatten_scored_actions(
            self._three_way_if_steps(), plain_map=self.plain, conditional_groups=self.conditional, _branch_groups=branch_groups
        )
        core_uids = {a.uid for a in out}
        # 예측이 elseIf#1(두 번째 분기)만 정확히 구현했다고 가정.
        matched_uids = set(branch_groups[0]["branches"][1]["uids"])

        result = score_branch_coverage(branch_groups, matched_uids, core_uids)

        self.assertTrue(result["applicable"])
        self.assertEqual(result["scoreable_branch_count"], 3)
        self.assertAlmostEqual(result["branch_coverage"], 1 / 3)
        self.assertAlmostEqual(result["branch_score"], 1 / 3)

    def test_branch_coverage_is_all_or_nothing_per_branch_not_a_percentage_gate(self) -> None:
        """분기 하나에 액션 3개 중 2개만 매칭되면 그 분기는 Coverage 분자에서
        빠지되(all-or-nothing), Score에는 2/3로 부분 반영돼야 한다 - 임의
        임계값(예: 50% 이상이면 인정) 없이 두 지표가 서로 다른 역할을 하는지
        확인. if 그룹을 단일 분기(elseIf/else 없는 0376의 첫 if처럼)로 둬서
        다른 분기의 0점이 평균에 섞이지 않게 한다."""
        branch_groups = [{"group_id": "if#1", "branches": [{"branch_name": "if", "uids": ["a", "b", "c"]}]}]
        core_uids = {"a", "b", "c"}
        matched_uids = {"a", "b"}  # 3개 중 2개만 매칭

        result = score_branch_coverage(branch_groups, matched_uids, core_uids)

        self.assertEqual(result["branch_coverage"], 0.0)  # 완전매칭 분기 0개
        self.assertAlmostEqual(result["branch_score"], 2 / 3)  # 부분점수는 반영

    def test_non_ambiguous_branch_actions_do_not_reduce_coverage_when_excluded_as_non_core(self) -> None:
        """분기의 핵심 액션이 core-business 분류로 전부 제외되면(예: 로그성
        메시지박스뿐인 분기) coverage_ratio가 None이 되고 분모에서 빠져야
        한다 - 핵심 액션이 원래 없는 분기를 강제로 0점 처리하면 안 됨."""
        branch_groups = [{"group_id": "if#1", "branches": [{"branch_name": "if", "uids": ["a", "b"]}]}]
        core_uids: set[str] = set()  # 이 분기의 액션이 전부 non-core로 분류됨
        matched_uids: set[str] = set()

        result = score_branch_coverage(branch_groups, matched_uids, core_uids)

        self.assertFalse(result["applicable"])
        self.assertIsNone(result["branch_coverage"])
        self.assertIsNone(result["branch_score"])
        self.assertIsNone(result["groups"][0]["branches"][0]["coverage_ratio"])


class ClassifyCoreBusinessActionsTest(unittest.TestCase):
    """규칙(키워드) 우선, 잔여만 LLM. 테스트는 공유 캐시 파일을 건드리지
    않도록 _load_classification_cache/_save_classification_cache를 메모리
    dict로 패치한다(실제 운영 캐시와 섞이지 않게)."""

    def setUp(self) -> None:
        self._cache: dict = {}
        patcher_load = patch("action_matching._load_classification_cache", return_value=self._cache)
        patcher_save = patch("action_matching._save_classification_cache", side_effect=self._cache.update)
        patcher_load.start()
        patcher_save.start()
        self.addCleanup(patcher_load.stop)
        self.addCleanup(patcher_save.stop)

    def test_non_ambiguous_action_is_always_core_without_llm_call(self) -> None:
        action = ScoredAction("u1", "Excel_MS", "SetCell", "excel_ms.setcell", readable_params="cell=A1")
        with patch("action_matching.judge_core_business_relevance") as mock_judge:
            core, excluded, log = classify_core_business_actions(
                [action], case_id="case", business_definition="업무정의서 본문", case_title="테스트 업무"
            )
        mock_judge.assert_not_called()
        self.assertEqual(core, [action])
        self.assertEqual(excluded, [])
        self.assertEqual(log, [])

    def test_ambiguous_action_with_infrastructure_keyword_is_excluded_by_rule_without_llm_call(self) -> None:
        action = ScoredAction(
            "u2", "Folder", "createFolder", "folder.createfolder", readable_params="folderPath=$pStrLogsFolder$"
        )
        with patch("action_matching.judge_core_business_relevance") as mock_judge:
            core, excluded, log = classify_core_business_actions(
                [action], case_id="case", business_definition="업무정의서 본문", case_title="테스트 업무"
            )
        mock_judge.assert_not_called()
        self.assertEqual(core, [])
        self.assertEqual(excluded, [action])
        self.assertEqual(log[0]["source"], "rule")
        self.assertEqual(log[0]["verdict"], "not_core_business")

    def test_ambiguous_action_without_keyword_falls_back_to_llm(self) -> None:
        action = ScoredAction(
            "u3", "Folder", "createFolder", "folder.createfolder", readable_params="folderPath=$vArchiveFolderPath$"
        )
        with patch(
            "action_matching.judge_core_business_relevance",
            return_value={"verdict": "core_business", "reason": "업무 원문에 언급된 보관 폴더"},
        ) as mock_judge:
            core, excluded, log = classify_core_business_actions(
                [action], case_id="case", business_definition="업무정의서 본문", case_title="테스트 업무"
            )
        mock_judge.assert_called_once()
        self.assertEqual(core, [action])
        self.assertEqual(excluded, [])
        self.assertEqual(log[0]["source"], "llm")
        self.assertEqual(log[0]["verdict"], "core_business")
        # 캐시에 기록돼서 같은 case_id/label/params면 재호출 안 함
        cache_key = "case|Folder.createFolder|folderPath=$vArchiveFolderPath$"
        self.assertIn(cache_key, self._cache)

        with patch("action_matching.judge_core_business_relevance") as mock_judge_second_call:
            classify_core_business_actions(
                [action], case_id="case", business_definition="업무정의서 본문", case_title="테스트 업무"
            )
        mock_judge_second_call.assert_not_called()

    def test_no_business_definition_skips_classification_entirely(self) -> None:
        action = ScoredAction(
            "u4", "Folder", "createFolder", "folder.createfolder", readable_params="folderPath=$vArchiveFolderPath$"
        )
        with patch("action_matching.judge_core_business_relevance") as mock_judge:
            core, excluded, log = classify_core_business_actions(
                [action], case_id=None, business_definition=None, case_title=None
            )
        mock_judge.assert_not_called()
        self.assertEqual(core, [action])
        self.assertEqual(excluded, [])
        self.assertEqual(log, [])


if __name__ == "__main__":
    unittest.main()

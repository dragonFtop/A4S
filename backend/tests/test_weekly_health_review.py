"""周定时体检写入回顾报告的单元测试（P3）。

只验证「体检摘要 → Markdown 第六段」的确定性行为，不碰数据库、不调模型。
"""

import unittest

from client.services.review_service import _health_action, _section_health


class HealthSectionTests(unittest.TestCase):
    def test_lists_findings_and_action(self) -> None:
        health = {
            "run_id": "r-1",
            "score": 77,
            "documents": 4,
            "counts": {"high": 1, "medium": 1, "low": 0},
            "findings": [
                {"claim": "《高数.pdf》没有可抽取文本", "severity": "high", "check": "no_chunks"},
                {"claim": "《线代.pdf》没有被检索到", "severity": "medium", "check": "probe_missed"},
            ],
            "proposals": [
                {"label": "重新解析或改用手动粘贴", "action": "reparse", "finding_id": "f-1"}
            ],
        }
        text = _section_health(health)
        self.assertIn("## 六、资料体检", text)
        self.assertIn("健康分 **77**，2 个问题待处理", text)
        self.assertIn("[待修复] 《高数.pdf》没有可抽取文本", text)
        self.assertIn("[建议] 《线代.pdf》没有被检索到", text)
        self.assertIn("**下一步**：先在知识库页重新解析或改用手动粘贴", text)

    def test_findings_beyond_five_are_summarized(self) -> None:
        findings = [{"claim": f"问题 {index}", "severity": "low"} for index in range(1, 8)]
        text = _section_health({"score": 79, "findings": findings, "proposals": []})
        self.assertIn("还有 2 条", text)
        self.assertNotIn("问题 6", text)

    def test_zero_findings_still_has_next_step(self) -> None:
        text = _section_health({"score": 100, "findings": [], "proposals": []})
        self.assertIn("健康分 **100**，0 个问题待处理", text)
        self.assertIn("按本期建议继续即可", text)

    def test_error_degrades_to_one_line(self) -> None:
        text = _section_health({"error": "Embedding 模型不可用"})
        self.assertIn("本次体检没有完成：Embedding 模型不可用", text)
        self.assertIn("不影响本期回顾", text)
        self.assertNotIn("健康分", text)

    def test_action_prefers_fix_proposal(self) -> None:
        findings = [{"claim": "x", "severity": "high"}]
        self.assertEqual(
            _health_action({"proposals": [{"label": "重建索引"}]}, findings),
            "先在知识库页重建索引，处理完再体检一次。",
        )
        self.assertIn("上面列出的资料问题", _health_action({"proposals": []}, findings))
        self.assertIn("按本期建议继续即可", _health_action({"proposals": []}, []))


if __name__ == "__main__":
    unittest.main()
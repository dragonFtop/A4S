"""探针召回（P2.1）的单元测试。

不连数据库、不加载 Embedding：片段仓储与检索函数都换成桩，
只验证 `probe_recall` 自己的判断逻辑——抽哪些资料、怎么算命中、
未命中怎么出 Finding、检索挂了怎么降级。

运行（在 backend 目录）：
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from client.services.agents import probe
from shared.domain.models.agents import AgentRole, FindingKind
from shared.domain.models.entities import DocumentStatus


class _FakeChunkRepo:
    """按资料 id 返回预置片段，替代 SqlAlchemyChunkRepository。"""

    def __init__(self, chunks: dict[str, list[SimpleNamespace]]) -> None:
        self._chunks = chunks

    def list_by_document(self, document_id: str) -> list[SimpleNamespace]:
        return self._chunks.get(document_id, [])


def _doc(doc_id: str, name: str, status: DocumentStatus = DocumentStatus.READY):
    return SimpleNamespace(id=doc_id, name=name, status=status)


def _chunk(text: str):
    return SimpleNamespace(text=text)


def _cite(doc_id: str, name: str) -> dict:
    return {"document_id": doc_id, "document_name": name}


class ProbeRecallTests(unittest.TestCase):
    def _run(self, docs, chunks, retrieve):
        """跑一次探针，返回 (报告, 检索桩)。"""
        repo = _FakeChunkRepo(chunks)
        with (
            mock.patch.object(probe, "SqlAlchemyChunkRepository", lambda db: repo),
            mock.patch.object(probe.retrieval, "retrieve", side_effect=retrieve) as fake,
        ):
            report = probe.probe_recall(object(), space_id="s-1", docs=docs)
        return report, fake

    def test_hit_produces_no_finding(self):
        docs = [_doc("d-1", "资料一")]
        chunks = {"d-1": [_chunk("甲" * 300)]}

        def retrieve(db, question, *, space_id):
            return [_cite("d-1", "资料一")], [], 0.9

        report, fake = self._run(docs, chunks, retrieve)
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(
            (report.checked, report.hits, report.missed, report.skipped),
            (1, 1, 0, 0),
        )
        self.assertEqual(report.findings, [])
        self.assertNotIn("没有被召回", report.brief)

    def test_miss_produces_finding_with_evidence(self):
        docs = [_doc("d-1", "资料一")]
        chunks = {"d-1": [_chunk("甲" * 300)]}
        hits = [_cite(f"d-{i}", f"别的资料{i}") for i in range(2, 6)]

        def retrieve(db, question, *, space_id):
            return hits, [], 0.8

        report, _ = self._run(docs, chunks, retrieve)
        self.assertEqual(report.missed, 1)
        self.assertEqual(len(report.findings), 1)

        finding = report.findings[0]
        self.assertEqual(finding.kind, FindingKind.DOCUMENT)
        self.assertEqual(finding.producer, AgentRole.INSPECTOR.value)
        self.assertIn("资料一", finding.claim)

        evidence = finding.evidence
        self.assertEqual(evidence["check"], "probe_missed")
        self.assertEqual(evidence["severity"], "medium")
        self.assertEqual(evidence["document_id"], "d-1")
        self.assertEqual(evidence["document_ids"], ["d-1"])
        self.assertEqual(evidence["probe_chars"], probe.PROBE_CHARS)
        # 证据里的原文只留 120 字，避免报告膨胀
        self.assertEqual(len(evidence["probe"]), 120)
        # 只记前 3 条实际命中，够排查即可
        self.assertEqual(len(evidence["hits"]), 3)
        self.assertIn("没有被召回", report.brief)

    def test_uses_longest_chunk_and_truncates_probe(self):
        docs = [_doc("d-1", "资料一")]
        long_text = "长" * 500
        chunks = {"d-1": [_chunk("短片段" * 5), _chunk(long_text)]}
        captured: list[str] = []

        def retrieve(db, question, *, space_id):
            captured.append(question)
            return [_cite("d-1", "资料一")], [], 1.0

        self._run(docs, chunks, retrieve)
        self.assertEqual(captured, [long_text[: probe.PROBE_CHARS]])

    def test_short_chunks_are_skipped_without_retrieval(self):
        docs = [_doc("d-1", "短资料"), _doc("d-2", "没有片段")]
        chunks = {"d-1": [_chunk("短" * (probe.MIN_PROBE_CHARS - 1))]}

        report, fake = self._run(docs, chunks, lambda db, q, *, space_id: ([], [], 0.0))
        fake.assert_not_called()
        self.assertEqual(report.skipped, 2)
        self.assertEqual(report.checked, 0)
        self.assertEqual(report.findings, [])
        self.assertIn("没有可抽测的资料", report.reason)
        self.assertEqual(report.brief, report.reason)

    def test_not_ready_document_is_ignored(self):
        docs = [_doc("d-1", "待审资料", DocumentStatus.PENDING_REVIEW)]
        chunks = {"d-1": [_chunk("甲" * 300)]}

        report, fake = self._run(docs, chunks, lambda db, q, *, space_id: ([], [], 0.0))
        fake.assert_not_called()
        self.assertEqual(report.checked, 0)
        self.assertEqual(report.findings, [])

    def test_probe_count_is_capped(self):
        total = probe.MAX_PROBES + 2
        docs = [_doc(f"d-{i}", f"资料{i}") for i in range(total)]
        chunks = {f"d-{i}": [_chunk("正" * 100)] for i in range(total)}

        report, fake = self._run(docs, chunks, lambda db, q, *, space_id: ([], [], 0.0))
        self.assertEqual(fake.call_count, probe.MAX_PROBES)
        self.assertEqual(report.checked, probe.MAX_PROBES)
        self.assertEqual(report.missed, probe.MAX_PROBES)
        self.assertEqual(len(report.findings), probe.MAX_PROBES)

    def test_retrieval_error_degrades_without_findings(self):
        docs = [_doc("d-1", "资料一")]
        chunks = {"d-1": [_chunk("甲" * 300)]}

        def retrieve(db, question, *, space_id):
            raise RuntimeError("Embedding 模型不可用")

        report, _ = self._run(docs, chunks, retrieve)
        self.assertEqual(report.checked, 0)
        self.assertEqual(report.findings, [])
        self.assertIn("检索不可用", report.reason)
        self.assertIn("Embedding 模型不可用", report.reason)

    def test_earlier_hits_are_kept_when_later_probe_fails(self):
        docs = [_doc("d-1", "资料一"), _doc("d-2", "资料二")]
        chunks = {"d-1": [_chunk("甲" * 300)], "d-2": [_chunk("乙" * 300)]}
        calls = {"n": 0}

        def retrieve(db, question, *, space_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return [_cite("d-1", "资料一")], [], 0.9
            raise RuntimeError("索引维度不匹配")

        report, _ = self._run(docs, chunks, retrieve)
        self.assertEqual((report.checked, report.hits, report.missed), (1, 1, 0))
        self.assertEqual(report.findings, [])
        self.assertIn("索引维度不匹配", report.reason)


if __name__ == "__main__":
    unittest.main()
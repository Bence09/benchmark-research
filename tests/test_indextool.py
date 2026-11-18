"""Offline tests for the index tool (E2).

- Boundary tests use a FakePayload so they run without rank_bm25.
- Chunker tests use a synthetic repo on a tmp dir.
- BM25 payload tests skip cleanly when rank_bm25 is absent.
"""

import tempfile
import unittest
from pathlib import Path

from indextool import BuildInfo, IndexPayload, Region, ToolBoundary
from indextool.chunking import chunk_repo


class FakePayload:
    """A payload that records the top_k it was asked for and returns fixed regions."""

    kind = "fake"

    def __init__(self):
        self.last_top_k = None
        self.raise_on_query = False

    def build(self, repo_root):
        return BuildInfo("fake", 0.0, 0, "deadbeef", "fake.v1", True, 0)

    def query(self, text, top_k):
        self.last_top_k = top_k
        if self.raise_on_query:
            raise ValueError("boom")
        return [
            Region(path="pkg/a.py", start=1, end=80, score=7.25, snippet="x" * 5000),
            Region(path="pkg/b.py", start=10, end=20, score=3.1, snippet="short"),
        ][:top_k]


class TestPayloadProtocol(unittest.TestCase):
    def test_fake_satisfies_protocol(self):
        self.assertIsInstance(FakePayload(), IndexPayload)


class TestBoundary(unittest.TestCase):
    def setUp(self):
        self.payload = FakePayload()
        self.boundary = ToolBoundary(self.payload, max_top_k=10, max_snippet_bytes=2048)

    def test_basic_call_returns_regions_and_record(self):
        obs, rec = self.boundary.handle('index_query "memory leak in cache"')
        self.assertIn("pkg/a.py:1-80", obs)
        self.assertEqual(rec.tool, "index_query")
        self.assertEqual(rec.query, "memory leak in cache")
        self.assertEqual(len(rec.returned_regions), 2)
        self.assertIsNone(rec.error)
        self.assertFalse(rec.denied)
        self.assertEqual(rec.channel, "index_query")

    def test_top_k_clamped_to_limit(self):
        self.boundary.handle('index_query "q" --top-k 999')
        self.assertEqual(self.payload.last_top_k, 10)  # clamped

    def test_top_k_respected_when_below_limit(self):
        _, rec = self.boundary.handle('index_query "q" --top-k 1')
        self.assertEqual(self.payload.last_top_k, 1)
        self.assertEqual(len(rec.returned_regions), 1)

    def test_snippet_truncated_to_max_bytes(self):
        _, rec = self.boundary.handle('index_query "q" --top-k 1')
        # a.py snippet was 5000 bytes; delivered must be capped at 2048.
        self.assertLessEqual(rec.bytes_delivered, 2048)

    def test_empty_query_is_recorded_error_not_crash(self):
        obs, rec = self.boundary.handle('index_query ""')
        self.assertIsNotNone(rec.error)
        self.assertIn("error", obs)

    def test_bad_top_k_recorded(self):
        _, rec = self.boundary.handle('index_query "q" --top-k abc')
        self.assertIsNotNone(rec.error)

    def test_payload_exception_becomes_observation(self):
        self.payload.raise_on_query = True
        _obs, rec = self.boundary.handle('index_query "q"')
        self.assertIn("ValueError", rec.error)
        self.assertIsNotNone(rec.latency_seconds)

    def test_record_serializes(self):
        _, rec = self.boundary.handle('index_query "q"')
        d = rec.to_dict()
        self.assertEqual(d["channel"], "index_query")
        self.assertIn("returned_regions", d)


class TestChunker(unittest.TestCase):
    def _make_repo(self, root: Path):
        (root / "pkg").mkdir(parents=True)
        (root / "pkg" / "a.py").write_text("\n".join(f"line{i}" for i in range(1, 201)))
        (root / "pkg" / "b.py").write_text("def f():\n    return 1\n")
        (root / "notes.md").write_text("# title\nbody\n")
        (root / "image.png").write_bytes(b"\x89PNG not source")

    def test_chunks_are_1_based_inclusive_and_skip_binaries(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._make_repo(root)
            chunks = chunk_repo(root, chunk_size=80, chunk_overlap=20)
            self.assertTrue(chunks)
            # no png chunked
            self.assertFalse(any(c.path.endswith(".png") for c in chunks))
            # first chunk of a 200-line file starts at line 1
            a_chunks = [c for c in chunks if c.path == "pkg/a.py"]
            self.assertEqual(a_chunks[0].start, 1)
            self.assertGreaterEqual(a_chunks[0].end, 80)
            # overlapping windows: second chunk starts at step+1 = 61
            self.assertEqual(a_chunks[1].start, 61)

    def test_max_chunks_cap(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root).mkdir(exist_ok=True)
            big = "\n".join(f"line{i}" for i in range(1, 100000))
            (root / "huge.py").write_text(big)
            chunks = chunk_repo(root, chunk_size=10, chunk_overlap=0, max_chunks=50)
            self.assertLessEqual(len(chunks), 50)


try:
    import rank_bm25  # noqa: F401

    from indextool.bm25 import BM25Payload

    _HAS_BM25 = True
except ImportError:
    _HAS_BM25 = False


@unittest.skipUnless(_HAS_BM25, "rank_bm25 not installed")
class TestBM25Payload(unittest.TestCase):
    def _make_repo(self, root: Path):
        (root / "cache.py").write_text(
            "class Cache:\n    def evict(self):\n        # free memory\n        pass\n"
        )
        (root / "unrelated.py").write_text("def add(a, b):\n    return a + b\n")

    def test_build_and_query(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._make_repo(root)
            payload = BM25Payload(chunk_size=20, chunk_overlap=5)
            info = payload.build(root)
            self.assertEqual(info.kind, "bm25")
            self.assertTrue(info.leakage_checks_passed)
            self.assertGreater(info.chunk_count, 0)
            self.assertRegex(info.input_repo_sha256, r"^[0-9a-f]{64}$")
            regions = payload.query("free memory from cache eviction", top_k=1)
            self.assertEqual(len(regions), 1)
            self.assertEqual(regions[0].path, "cache.py")

    def test_leakage_check_rejects_grading_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._make_repo(root)
            (root / "benchmark.grading.json").write_text('{"gold": 1}')
            payload = BM25Payload()
            with self.assertRaises(RuntimeError):
                payload.build(root)


if __name__ == "__main__":
    unittest.main()

import io
import runpy
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

BENCHMARK_PATH = Path(__file__).resolve().parents[1] / "tools" / "benchmark_core.py"
NATIVE_EXPORTS = (
    "parse_danmaku_xml",
    "sign_wbi_params",
    "bv2av",
    "av2bv",
    "generate_vod_mpd",
    "render_reply_content",
    "unpack_live_danmaku",
)


class TestBenchmarkCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load application dependencies before patch.dict restores sys.modules.
        try:
            runpy.run_path(str(BENCHMARK_PATH))
        except SystemExit:
            pass  # Native support is optional in the application test suite.

    def test_requires_native_import(self):
        output = io.StringIO()
        with patch.dict(sys.modules, {"miku_core_rs": None}), redirect_stdout(output):
            with self.assertRaisesRegex(SystemExit, "requires miku_core_rs"):
                runpy.run_path(str(BENCHMARK_PATH), run_name="__main__")
        self.assertNotIn("Benchmark run complete", output.getvalue())

    def test_requires_every_native_export(self):
        for missing in NATIVE_EXPORTS:
            with self.subTest(missing=missing):
                native = SimpleNamespace(**{name: Mock() for name in NATIVE_EXPORTS if name != missing})
                output = io.StringIO()
                with patch.dict(sys.modules, {"miku_core_rs": native}), redirect_stdout(output):
                    with self.assertRaisesRegex(SystemExit, missing):
                        runpy.run_path(str(BENCHMARK_PATH), run_name="__main__")
                self.assertNotIn("Benchmark run complete", output.getvalue())

    def test_bvid_reports_conversion_pairs(self):
        native = SimpleNamespace(**{name: Mock() for name in NATIVE_EXPORTS})
        output = io.StringIO()
        with patch.dict(sys.modules, {"miku_core_rs": native}), redirect_stdout(output):
            benchmark = runpy.run_path(str(BENCHMARK_PATH))
            benchmark["benchmark_bvid"](iterations=1)
        native.bv2av.assert_called_once_with("BV17x411w7KC")
        native.av2bv.assert_called_once_with(170001)
        self.assertEqual(output.getvalue().count("µs/pair | pairs/sec="), 2)
        self.assertNotIn("ops/sec", output.getvalue())


if __name__ == "__main__":
    unittest.main()

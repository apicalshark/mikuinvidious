import contextlib
import io
import os
import sys
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
sys.path.insert(0, str(ROOT / "python"))


def _configure_env() -> None:
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
    os.environ.setdefault("no_proxy", "localhost,127.0.0.1,::1")
    py_dir = str(ROOT / "python")
    existing = os.environ.get("PYTHONPATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if py_dir not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([py_dir, *parts])
    if py_dir not in sys.path:
        sys.path.insert(0, py_dir)
    tests_str = str(TESTS_DIR)
    if tests_str not in sys.path:
        sys.path.insert(0, tests_str)


def _run_module_batch(module_names: list[str]) -> tuple[bool, str]:
    _configure_env()

    import_buf = io.StringIO()
    with contextlib.redirect_stdout(import_buf), contextlib.redirect_stderr(import_buf):
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for name in module_names:
            suite.addTests(loader.loadTestsFromName(name))

    runner_buf = io.StringIO()
    runner = unittest.TextTestRunner(stream=runner_buf, verbosity=0, buffer=True)
    result = runner.run(suite)
    if not result.wasSuccessful():
        return False, runner_buf.getvalue()
    return True, ""


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
    os.environ.setdefault("no_proxy", "localhost,127.0.0.1,::1")
    targets = argv if argv is not None else sys.argv[1:]

    if targets:
        ok, output = _run_module_batch(targets)
        if not ok and output:
            sys.stderr.write(output)
        return 0 if ok else 1

    modules = sorted(p.stem for p in TESTS_DIR.glob("test_*.py"))
    workers = min(4, os.cpu_count() or 2, len(modules))
    if workers <= 1:
        ok, output = _run_module_batch(modules)
        if not ok and output:
            sys.stderr.write(output)
        return 0 if ok else 1

    buckets: list[list[str]] = [[] for _ in range(workers)]
    for idx, mod in enumerate(modules):
        buckets[idx % workers].append(mod)

    failed_outputs: list[str] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for ok, output in pool.map(_run_module_batch, buckets):
            if not ok and output:
                failed_outputs.append(output)

    if failed_outputs:
        sys.stderr.write("\n".join(failed_outputs))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

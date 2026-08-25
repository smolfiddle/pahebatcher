#!/usr/bin/env python3
"""Coherence benchmark — runs tests + lint + typecheck and reports metrics."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], capture: bool = True) -> tuple[int, str, str, float]:
    start = time.time()
    result = subprocess.run(cmd, cwd=ROOT, capture_output=capture, text=True)
    elapsed = time.time() - start
    return result.returncode, result.stdout, result.stderr, elapsed


def main() -> None:
    print("=== pahebatcher coherence benchmark ===\n")

    # 1. ruff
    print("--- ruff ---")
    code, out, err, t = run([sys.executable, "-m", "ruff", "check", "src/"])
    if code == 0:
        print(f"  ruff: OK (0 errors) in {t:.2f}s")
    else:
        print(f"  ruff: FAIL ({code})\n{out}\n{err}")
        sys.exit(1)

    # 2. mypy
    print("\n--- mypy strict ---")
    code, out, err, t = run([sys.executable, "-m", "mypy", "src/"])
    if code == 0:
        print(f"  mypy: OK (0 errors) in {t:.2f}s")
    else:
        print(f"  mypy: FAIL\n{out}\n{err}")
        sys.exit(1)

    # 3. pytest
    print("\n--- pytest ---")
    code, out, err, t = run([sys.executable, "-m", "pytest", "tests/", "-q"])
    print(out.strip().splitlines()[-1] if out else "")
    if code != 0:
        print(err)
        sys.exit(1)
    # Extract count
    import re

    m = re.search(r"(\d+) passed", out)
    passed = int(m.group(1)) if m else 0
    m2 = re.search(r"(\d+) failed", out)
    failed = int(m2.group(1)) if m2 else 0
    print(f"  pytest: {passed} passed, {failed} failed in {t:.2f}s")

    # 4. coverage (optional)
    print("\n--- coverage ---")
    code, out, err, t = run(
        [sys.executable, "-m", "pytest", "tests/", "--cov=pahebatcher", "--cov-report=term-missing", "-q"]
    )
    if code == 0:
        # Find coverage line
        for line in out.splitlines():
            if "TOTAL" in line and "%" in line:
                print(f"  coverage: {line.strip()}")
    else:
        print("  coverage: pytest-cov not installed or failed (install with pip install pytest-cov)")

    # 5. loc
    print("\n--- loc ---")
    src_files = list((ROOT / "src").rglob("*.py"))
    src_lines = sum(len((p).read_text(errors="ignore").splitlines()) for p in src_files)
    test_files = list((ROOT / "tests").rglob("*.py"))
    test_lines = sum(len((p).read_text(errors="ignore").splitlines()) for p in test_files)
    print(f"  src files: {len(src_files)}, src loc: {src_lines}")
    print(f"  test files: {len(test_files)}, test loc: {test_lines}")
    density = (passed / src_lines * 100) if src_lines else 0
    print(f"  test density: {density:.2f} per 100 LOC")

    # 6. coherence summary
    print("\n--- coherence ---")
    from pahebatcher import __version__
    from pahebatcher.config import VERSION

    assert __version__ == VERSION
    print(f"  version: {VERSION} OK")
    from pahebatcher.config_manager import ConfigManager
    from pahebatcher.models import AppContext

    cm = ConfigManager()
    app = AppContext.defaults()
    assert cm.DEFAULTS["quality"] == app.quality
    print("  config defaults: OK")
    print("\n=== benchmark complete: all checks green ===")


if __name__ == "__main__":
    main()

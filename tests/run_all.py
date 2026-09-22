"""Run every offline harness. ``python tests/run_all.py``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HARNESSES = ("test_protocol.py", "test_cast.py", "test_camera.py")


def main() -> int:
    """Run each harness in its own process; the stubs are global to a process."""
    here = Path(__file__).resolve().parent
    failed = 0
    for name in HARNESSES:
        result = subprocess.run([sys.executable, str(here / name)], check=False)
        failed += result.returncode != 0
    if failed:
        print(f"{failed} harness(es) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

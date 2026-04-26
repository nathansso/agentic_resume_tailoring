#!/usr/bin/env python
"""Run the full ART test suite.

Usage:
  python run_tests.py              # run all tests
  python run_tests.py -k chat      # filter by keyword
  python run_tests.py -v           # verbose
  python run_tests.py --integration  # include slow/network tests (custom flag)
"""
import subprocess
import sys

extra = sys.argv[1:]

# By default skip integration tests (require network + real DB).
# Pass --integration to include them.
include_integration = "--integration" in extra
if include_integration:
    extra = [a for a in extra if a != "--integration"]
else:
    extra = ["-m", "not integration"] + extra

cmd = [sys.executable, "-m", "pytest", "tests/", "-q"] + extra
sys.exit(subprocess.run(cmd).returncode)

"""Throwaway for #4139. Never merge: fails on Python 3.12 on purpose."""

import sys


def test_fails_on_python_312_only() -> None:
    assert sys.version_info[:2] != (3, 12), "deliberate failure to show #4139's summary legs"

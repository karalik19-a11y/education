#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точка входа «Книжный страж» (работает и из исходников, и из venv)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def in_venv():
    return (VENV / "bin" / "python").exists() or (VENV / "Scripts" / "python.exe").exists()


def venv_python():
    win = VENV / "Scripts" / "python.exe"
    return win if win.exists() else VENV / "bin" / "python"


if in_venv() and sys.prefix != str(VENV):
    os.execv(str(venv_python()), [str(venv_python()), str(ROOT / "run.py")] + sys.argv[1:])

sys.path.insert(0, str(ROOT))
from bookguard.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()

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


def _same_path(a, b):
    # realpath: без него на симлинкованных путях (типа /tmp на macOS)
    # сравнение sys.prefix никогда не сходилось и run.py бесконечно
    # пере-запускал сам себя, не доходя ни до каких сообщений.
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return a == b


if in_venv() and not _same_path(sys.executable, str(venv_python())):
    os.execv(str(venv_python()), [str(venv_python()), str(ROOT / "run.py")] + sys.argv[1:])

sys.path.insert(0, str(ROOT))
from bookguard.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()

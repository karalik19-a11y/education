#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точка входа «Книжного стража».

Самочистящаяся обёртка:
  * если рядом лежит .venv — прозрачно перезапускается тем же интерпретатором
    (Windows: subprocess, POSIX: os.execv — старые версии падали здесь,
    из-за чего программа «не открывалась»);
  * любые ошибки startup пишутся в data/app.log и показываются пользователю
    (ярлык не молчит и не «тупит»);
  * код выхода всегда осмысленный — run.bat/run.sh умеют повторять запуск.
"""
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
IS_WIN = os.name == "nt"


def venv_python():
    win = VENV / "Scripts" / "python.exe"
    return win if win.exists() else VENV / "bin" / "python"


def in_venv():
    return venv_python().exists()


def log_error(tag, tb):
    try:
        (ROOT / "data").mkdir(parents=True, exist_ok=True)
        with open(ROOT / "data" / "app.log", "a", encoding="utf-8") as f:
            import datetime
            f.write(f"\n===== {datetime.datetime.now()} {tag} =====\n{tb}\n")
    except Exception:  # noqa: BLE001
        pass


def reexec_into_venv():
    """Windows не умеет os.execv надёжно — используем subprocess."""
    py = str(venv_python())
    if IS_WIN:
        import subprocess
        code = subprocess.call([py, str(ROOT / "run.py")] + sys.argv[1:])
        sys.exit(code)
    else:
        os.execv(py, [py, str(ROOT / "run.py")] + sys.argv[1:])


def main():
    if in_venv() and str(Path(sys.prefix).resolve()) != str(VENV.resolve()):
        try:
            reexec_into_venv()
        except Exception:  # noqa: BLE001
            # не страшно: работаем текущим интерпретатором
            pass

    sys.path.insert(0, str(ROOT))
    try:
        from bookguard.__main__ import main as bg_main
        bg_main()
        return 0
    except SystemExit as e:
        return int(e.code or 0)
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        print(tb)
        log_error("run.py", tb)
        # Показываем причину даже если Tk нет (иначе — в консоль/лог)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Книжный страж — ошибка запуска",
                "Программа не смогла стартовать.\n\n"
                f"Причина (первая строка): {tb.strip().splitlines()[-1][:200]}\n\n"
                f"Полный лог: {ROOT / 'data' / 'app.log'}\n"
                "Если ошибка в зависимостях — запустите install.bat / install.sh.",
            )
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Автозапуск «Книжного стража» вместе с системой.

Принцип надёжности — резервирование: включается СРАЗУ НЕСКОЛЬКО
независимых механизмов, чтобы программа стартовала при входе в систему,
даже если один из них подвёл (отключён пользователем, нет прав и т.п.).

Windows:  1) задача в Планировщике (ONLOGON, с повышенными правами);
          2) запасной ключ реестра HKCU\\...\\Run;
          3) запасной ярлык в папке «Автозагрузка».
Linux:    файл .config/autostart/bookguard.desktop (XDG Autostart).
macOS:    LaunchAgent ~/Library/LaunchAgents/com.bookguard.app.plist.

Проверка: ``python -m bookguard autostart doctor``.
"""
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

from . import config

logger = logging.getLogger("bookguard")

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

TASK_NAME = "BookGuard"                 # имя задачи в Планировщике Windows
REG_VALUE = "BookGuard"                 # имя значения в реестре (ASCII — надёжнее)
LINUX_DESKTOP = "bookguard.desktop"
MAC_LABEL = "com.bookguard.app"
WIN_LNK_NAME = "BookGuard.lnk"          # ярлык в папке «Автозагрузка»


def _run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _runner_script() -> str:
    """Команда запуска программы."""
    if IS_WINDOWS:
        return str(config.ROOT / "run.bat")
    return str(config.ROOT / "run.sh")


# ------------------------------------------------------------------ Windows
def _win_schtasks(enable: bool):
    if enable:
        # Небольшая задержка после входа: рабочий стол и сеть успевают
        # проснуться, окно открывается надёжнее. Старые системы ключ /DELAY
        # не понимают — тогда создаём задачу без задержки.
        tr = f'"{_runner_script()}"'
        base = ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
                "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"]
        ok, out = _run(base + ["/DELAY", "0000:30"])
        if ok:
            return True, out
        return _run(base)
    ok, out = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    return ok, out


def _win_schtasks_enabled() -> bool:
    ok, _ = _run(["schtasks", "/Query", "/TN", TASK_NAME])
    return ok


def _win_registry(enable: bool):
    try:
        import winreg  # noqa: WPS433
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, REG_VALUE, 0, winreg.REG_SZ, f'"{_runner_script()}"')
            else:
                for name in (REG_VALUE, "Книжный страж"):  # второй — от старых версий
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _win_registry_enabled() -> bool:
    try:
        import winreg
        key_path = r"Software\\Microsoft\\Windows\\CurrentVersion\\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            for name in (REG_VALUE, "Книжный страж"):
                try:
                    winreg.QueryValueEx(key, name)
                    return True
                except FileNotFoundError:
                    continue
        return False
    except Exception:  # noqa: BLE001
        return False


def _win_startup_lnk_path() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup" / WIN_LNK_NAME)


def _win_startup_lnk(enable: bool):
    """Ярлык в папке «Автозагрузка» — третий страховочный механизм."""
    lnk = _win_startup_lnk_path()
    if not enable:
        try:
            if lnk.exists():
                lnk.unlink()
            return True, ""
        except Exception as e:  # noqa: BLE001
            return False, str(e)
    try:
        lnk.parent.mkdir(parents=True, exist_ok=True)
        # пути передаём через переменные окружения — не страшны пробелы,
        # русские буквы и кавычки в пути установки
        ps = ("$s=(New-Object -ComObject WScript.Shell)."
              "CreateShortcut($env:BOOKGUARD_LNK);"
              "$s.TargetPath=$env:BOOKGUARD_RUN;"
              "$s.WorkingDirectory=$env:BOOKGUARD_ROOT;"
              "$s.Description='Bookguard autostart';$s.Save()")
        env = dict(os.environ)
        env["BOOKGUARD_LNK"] = str(lnk)
        env["BOOKGUARD_RUN"] = _runner_script()
        env["BOOKGUARD_ROOT"] = str(config.ROOT)
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-Command", ps],
                           capture_output=True, text=True, timeout=30, env=env)
        if p.returncode == 0 and lnk.exists():
            return True, str(lnk)
        return False, ((p.stdout or "") + (p.stderr or "")).strip()[:300]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _win_startup_lnk_enabled() -> bool:
    try:
        return _win_startup_lnk_path().exists()
    except Exception:  # noqa: BLE001
        return False


def _win_enabled():
    return (_win_schtasks_enabled() or _win_registry_enabled()
            or _win_startup_lnk_enabled())


# ------------------------------------------------------------------ Linux
def _linux_desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / LINUX_DESKTOP


def _quote_desktop(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def render_desktop_entry() -> str:
    root = str(config.ROOT)
    run_sh = str(config.ROOT / "run.sh")
    # run.sh сам делает cd в свой каталог, поэтому Exec простой и надёжный;
    # двойные кавычки держат пути с пробелами. Ключ Path — страховка.
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Книжный страж\n"
        "Comment=Блокирует Wi-Fi до чтения книги вслух\n"
        f'Exec=bash "{_quote_desktop(run_sh)}"\n'
        f"Path={root}\n"
        "Terminal=false\n"
        "StartupNotify=false\n"
        "NoDisplay=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-GNOME-Autostart-Delay=5\n"
        "X-KDE-autostart-after=panel\n"
        "X-MATE-Autostart-Delay=5\n"
    )


def _linux(enable: bool):
    path = _linux_desktop_path()
    if enable:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_desktop_entry(), encoding="utf-8")
            # исполняемый бит: часть рабочих столов требует его для доверия
            try:
                mode = path.stat().st_mode
                path.chmod(mode | 0o100)
            except Exception:  # noqa: BLE001
                pass
            return True, str(path)
        except Exception as e:  # noqa: BLE001
            return False, str(e)
    try:
        if path.exists():
            path.unlink()
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    return True, ""


def _linux_enabled():
    return _linux_desktop_path().exists()


# ------------------------------------------------------------------ macOS
def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def _mac(enable: bool):
    path = _mac_plist_path()
    if enable:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                "  <key>Label</key><string>" + MAC_LABEL + "</string>\n"
                "  <key>ProgramArguments</key><array>\n"
                f"    <string>/bin/bash</string><string>{config.ROOT / 'run.sh'}</string>\n"
                "  </array>\n"
                "  <key>RunAtLoad</key><true/>\n"
                "  <key>ThrottleInterval</key><integer>10</integer>\n"
                "</dict></plist>\n", encoding="utf-8")
            _run(["launchctl", "load", str(path)])
            return True, str(path)
        except Exception as e:  # noqa: BLE001
            return False, str(e)
    _run(["launchctl", "unload", str(path)])
    try:
        if path.exists():
            path.unlink()
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    return True, ""


def _mac_enabled():
    return _mac_plist_path().exists()


# ------------------------------------------------------------------ API
def enable():
    """Включить автозапуск (все доступные механизмы). -> (ok, описание)."""
    if IS_WINDOWS:
        results = []
        ok_task, out_task = _win_schtasks(True)
        results.append(("Планировщик задач «%s» (при входе, с правами админа)"
                        % TASK_NAME, ok_task, out_task))
        ok_reg, out_reg = _win_registry(True)
        results.append(("ключ реестра автозагрузки (запасной)", ok_reg, out_reg))
        ok_lnk, out_lnk = _win_startup_lnk(True)
        results.append(("ярлык в папке «Автозагрузка» (запасной)", ok_lnk, out_lnk))
        good = [d for d, ok, _ in results if ok]
        bad = [(d, o) for d, ok, o in results if not ok]
        for d, ok, o in results:
            logger.info("autostart win: %s -> %s %s", d, ok, (o or "")[:150])
        if good:
            text = "; ".join(good)
            if bad and not ok_task:
                text += (" (задача Планировщика не создалась — запустите "
                         "консоль от имени администратора и повторите: "
                         "блокировка Wi-Fi без прав админа не сработает)")
            return True, text
        err = "; ".join(f"{d}: {o}" for d, o in bad)[:400]
        return False, err or "неизвестная ошибка"
    if IS_MACOS:
        ok, info = _mac(True)
        logger.info("autostart mac: %s %s", ok, info)
        return ok, info if ok else (info or "неизвестная ошибка")[:300]
    ok, info = _linux(True)
    logger.info("autostart linux: %s %s", ok, info)
    return ok, info if ok else (info or "неизвестная ошибка")[:300]


def disable():
    if IS_WINDOWS:
        _win_schtasks(False)
        _win_registry(False)
        _win_startup_lnk(False)
        return (not _win_enabled()), ""
    if IS_MACOS:
        return _mac(False)
    return _linux(False)


def is_enabled() -> bool:
    if IS_WINDOWS:
        return _win_enabled()
    if IS_MACOS:
        return _mac_enabled()
    return _linux_enabled()


def describe() -> str:
    if not is_enabled():
        return "автозапуск выключен"
    if IS_WINDOWS:
        parts = []
        if _win_schtasks_enabled():
            parts.append(f"Планировщик задач «{TASK_NAME}»")
        if _win_registry_enabled():
            parts.append("реестр Windows")
        if _win_startup_lnk_enabled():
            parts.append("папка «Автозагрузка»")
        return "включён (" + ", ".join(parts) + ")"
    if IS_MACOS:
        return f"включён (LaunchAgent {MAC_LABEL})"
    return f"включён ({_linux_desktop_path()})"


def doctor():
    """Проверка автозапуска: [(механизм, включён?, подробности)].

    Используется командой ``autostart doctor`` и установщиками.
    """
    items = []
    runner = Path(_runner_script())
    items.append(("Файл запуска", runner.exists(),
                  str(runner) if runner.exists()
                  else f"НЕ НАЙДЕН: {runner}"))
    if IS_WINDOWS:
        items.append((f"Планировщик задач «{TASK_NAME}»",
                      _win_schtasks_enabled(),
                      "при входе в систему, с правами администратора"
                      if _win_schtasks_enabled()
                      else "нет задачи (нужен запуск от имени администратора)"))
        items.append(("Ключ реестра автозагрузки", _win_registry_enabled(),
                      "HKCU\\...\\Run" if _win_registry_enabled()
                      else "нет ключа"))
        items.append(("Ярлык в папке «Автозагрузка»", _win_startup_lnk_enabled(),
                      str(_win_startup_lnk_path()) if _win_startup_lnk_enabled()
                      else "нет ярлыка"))
    elif IS_MACOS:
        path = _mac_plist_path()
        items.append((f"LaunchAgent {MAC_LABEL}", path.exists(),
                      str(path) if path.exists() else "нет plist-файла"))
    else:
        path = _linux_desktop_path()
        ok = path.exists()
        detail = str(path)
        if ok:
            try:
                content = path.read_text(encoding="utf-8")
                if str(config.ROOT) not in content and "run.sh" not in content:
                    ok = False
                    detail = f"{path} (файл чужой — перезапишите: autostart on)"
            except Exception as e:  # noqa: BLE001
                ok = False
                detail = f"{path} (не читается: {e})"
        else:
            detail = f"нет файла {path}"
        items.append(("XDG autostart", ok, detail))
    return items


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "on":
        ok, info = enable()
        print("Автозапуск ВКЛЮЧЁН:", info if ok else f"ошибка: {info}")
    elif cmd == "off":
        disable()
        print("Автозапуск выключен.")
    elif cmd == "doctor":
        for name, ok, detail in doctor():
            print(f"  [{'ok' if ok else '--'}] {name}: {detail}")
    else:
        print("Автозапуск:", describe())

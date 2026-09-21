# -*- coding: utf-8 -*-
"""Автозапуск «Книжного стража» вместе с системой.

Windows:  задача в Планировщике (ONLOGON, с повышенными правами);
          запасной вариант — ключ реестра HKCU\\...\\Run.
Linux:    файл .config/autostart/bookguard.desktop (XDG Autostart).
macOS:    LaunchAgent ~/Library/LaunchAgents/com.bookguard.app.plist.
"""
import platform
import subprocess
import sys
from pathlib import Path

from . import config

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

TASK_NAME = "BookGuard"                 # имя задачи в Планировщике Windows
REG_VALUE = "Книжный страж"             # имя значения в реестре автозагрузки
LINUX_DESKTOP = "bookguard.desktop"
MAC_LABEL = "com.bookguard.app"


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
        tr = f'"{_runner_script()}"'
        ok, out = _run([
            "schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
            "/SC", "ONLOGON", "/RL", "HIGHEST", "/F",
        ])
        return ok, out
    ok, out = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    return ok, out


def _win_registry(enable: bool):
    try:
        import winreg  # noqa: WPS433
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, REG_VALUE, 0, winreg.REG_SZ, f'"{_runner_script()}"')
            else:
                try:
                    winreg.DeleteValue(key, REG_VALUE)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _win_enabled():
    ok, out = _run(["schtasks", "/Query", "/TN", TASK_NAME])
    if ok:
        return True
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            winreg.QueryValueEx(key, REG_VALUE)
        return True
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ Linux
def _linux_desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / LINUX_DESKTOP


def render_desktop_entry() -> str:
    root = config.ROOT
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Книжный страж\n"
        "Comment=Блокирует Wi-Fi до чтения книги вслух\n"
        f'Exec=bash -c "cd \'{root}\' && bash run.sh"\n'
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-KDE-autostart-after=panel\n"
    )


def _linux(enable: bool):
    path = _linux_desktop_path()
    if enable:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_desktop_entry(), encoding="utf-8")
        return True, str(path)
    if path.exists():
        path.unlink()
    return True, ""


def _linux_enabled():
    return _linux_desktop_path().exists()


# ------------------------------------------------------------------ macOS
def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def _mac(enable: bool):
    path = _mac_plist_path()
    if enable:
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
            "</dict></plist>\n", encoding="utf-8")
        _run(["launchctl", "load", str(path)])
        return True, str(path)
    _run(["launchctl", "unload", str(path)])
    if path.exists():
        path.unlink()
    return True, ""


def _mac_enabled():
    return _mac_plist_path().exists()


# ------------------------------------------------------------------ API
def enable():
    """Включить автозапуск. -> (ok, описание)."""
    if IS_WINDOWS:
        ok, out = _win_schtasks(True)
        if ok:
            return True, f"задача «{TASK_NAME}» в Планировщике (при входе в систему, с правами админа)"
        ok2, out2 = _win_registry(True)
        if ok2:
            return True, ("ключ автозагрузки реестра (без повышенных прав: "
                          "блокировка Wi-Fi будет требовать подтверждения UAC)")
        return False, (out or out2)[:300]
    if IS_MACOS:
        return _mac(True)
    return _linux(True)


def disable():
    if IS_WINDOWS:
        ok1, o1 = _win_schtasks(False)
        ok2, o2 = _win_registry(False)
        return (ok1 or ok2), (o1 + o2)
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
        ok, _ = _run(["schtasks", "/Query", "/TN", TASK_NAME])
        if ok:
            return f"включён (Планировщик задач «{TASK_NAME}», при входе в систему)"
        return "включён (автозагрузка Windows)"
    if IS_MACOS:
        return f"включён (LaunchAgent {MAC_LABEL})"
    return f"включён ({_linux_desktop_path()})"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "on":
        ok, info = enable()
        print("Автозапуск ВКЛЮЧЁН:", info if ok else f"ошибка: {info}")
    elif cmd == "off":
        disable()
        print("Автозапуск выключен.")
    else:
        print("Автозапуск:", describe())

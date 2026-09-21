# -*- coding: utf-8 -*-
"""Автозапуск «Книжного стража» вместе с системой.

Надёжность (человек должен гарантированно увидеть программу после
включения/перезагрузки):

Windows:  Ставим ОБА механизма сразу:
          * ключ реестра HKCU\\...\\Run        — срабатывает при входе в учётку;
          * задача Планировщика ONLOGON       — срабатывает при входе,
            с правами администратора (нужны для блокировки Wi-Fi).
          Страж — отдельный пункт (Run-ключ + задача BookGuardGuard),
          чтобы защита работала, даже если само приложение закрыто.
Linux:    два файла .desktop в ~/.config/autostart (приложение + страж).
macOS:    два LaunchAgent-файла (приложение + страж).
"""
import platform
import subprocess
import sys
from pathlib import Path

from . import config

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

TASK_NAME = "BookGuard"                 # приложение (Планировщик Windows)
GUARD_TASK_NAME = "BookGuardGuard"      # страж (Планировщик Windows)
REG_VALUE = "Книжный страж"             # Run-ключ приложения
GUARD_REG_VALUE = "BookGuardGuard"      # Run-ключ стража
LINUX_DESKTOP = "bookguard.desktop"
GUARD_LINUX_DESKTOP = "bookguard-guard.desktop"
MAC_LABEL = "com.bookguard.app"
GUARD_MAC_LABEL = "com.bookguard.guard"


def _run(cmd, timeout=25):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _runner_script() -> str:
    return str(config.ROOT / ("run.bat" if IS_WINDOWS else "run.sh"))


def _guard_script() -> str:
    return str(config.ROOT / ("guard.bat" if IS_WINDOWS else "run.sh guard"))


# ------------------------------------------------------------------ Windows
def _win_schtasks(task_name: str, target: str, enable: bool, highest: bool = False):
    if enable:
        args = [
            "schtasks", "/Create", "/TN", task_name, "/TR", f'"{target}"',
            "/SC", "ONLOGON", "/F",
        ]
        if highest:
            args += ["/RL", "HIGHEST"]
        ok, out = _run(args)
        return ok, out
    ok, out = _run(["schtasks", "/Delete", "/TN", task_name, "/F"])
    return ok, out


def _win_registry(value: str, command: str, enable: bool):
    try:
        import winreg  # noqa: WPS433
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, value, 0, winreg.REG_SZ,
                                  f'"{command}"' if not command.endswith(".bat")
                                  else f'cmd /c "{command}"')
            else:
                try:
                    winreg.DeleteValue(key, value)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _win_enabled_schtasks(task_name: str):
    ok, _ = _run(["schtasks", "/Query", "/TN", task_name])
    return ok


def _win_enabled_registry(value: str):
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_QUERY_VALUE) as key:
            winreg.QueryValueEx(key, value)
        return True
    except Exception:  # noqa: BLE001
        return False


def _win_is_batch(path: str) -> bool:
    return path.lower().endswith((".bat", ".cmd"))


# ------------------------------------------------------------------ Linux
def _linux_desktop_path(name: str) -> Path:
    return Path.home() / ".config" / "autostart" / name


def render_desktop_entry(name: str, comment: str, exec_cmd: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        f"Comment={comment}\n"
        f"Exec={exec_cmd}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-KDE-autostart-after=panel\n"
    )


def _linux(enable: bool, name: str, comment: str, exec_cmd: str, log=print):
    path = _linux_desktop_path(name)
    if enable:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_desktop_entry(name, comment, exec_cmd),
                        encoding="utf-8")
        log(f"[autostart] {path}")
        return True, str(path)
    if path.exists():
        path.unlink()
    return True, ""


def _linux_enabled(name: str):
    return _linux_desktop_path(name).exists()


# ------------------------------------------------------------------ macOS
def _mac_plist_path(label: str, program_args) -> Path:
    path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    return path


def _mac(enable: bool, label: str, args: list, log=print):
    path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if enable:
        path.parent.mkdir(parents=True, exist_ok=True)
        arr = "\n".join(f"    <string>{a}</string>" for a in args)
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            f"  <key>Label</key><string>{label}</string>\n"
            "  <key>ProgramArguments</key><array>\n"
            f"{arr}\n"
            "  </array>\n"
            "  <key>RunAtLoad</key><true/>\n"
            "</dict></plist>\n", encoding="utf-8")
        _run(["launchctl", "load", str(path)])
        log(f"[autostart] {path}")
        return True, str(path)
    _run(["launchctl", "unload", str(path)])
    if path.exists():
        path.unlink()
    return True, ""


def _mac_enabled(label: str):
    return (Path.home() / "Library" / "LaunchAgents" / f"{label}.plist").exists()


# ------------------------------------------------------------------ API
def _app_autostart(enable: bool, log=print):
    """Включить/выключить автозапуск ПРЛОЖЕНИЯ. -> (ok, описание)."""
    runner = _runner_script()
    if IS_WINDOWS:
        ok1, o1 = _win_schtasks(TASK_NAME, runner, enable, highest=True)
        ok2, o2 = _win_registry(REG_VALUE, runner, enable)
        if not (ok1 or ok2):
            return False, (o1 or o2)[:300]
        parts = []
        if ok1:
            parts.append(f"задача «{TASK_NAME}» (Планировщик, при входе, админ-права)")
        if ok2:
            parts.append("ключ автозагрузки реестра (при входе в учётную запись)")
        return True, " + ".join(parts)
    if IS_MACOS:
        return _mac(enable, MAC_LABEL, ["/bin/bash", runner], log=log)
    return _linux(enable, LINUX_DESKTOP, config.APP_NAME,
                  f"bash -c \"cd '{config.ROOT}' && bash run.sh\"", log=log)


def _guard_autostart(enable: bool, log=print):
    """Включить/выключить автозапуск СТРАЖА. -> (ok, описание)."""
    guard = _guard_script()
    if IS_WINDOWS:
        ok1, o1 = _win_schtasks(GUARD_TASK_NAME, guard, enable, highest=False)
        ok2, o2 = _win_registry(GUARD_REG_VALUE, guard, enable)
        if not (ok1 or ok2):
            return False, (o1 or o2)[:300]
        parts = []
        if ok1:
            parts.append(f"задача «{GUARD_TASK_NAME}»")
        if ok2:
            parts.append("Run-ключ «BookGuardGuard»")
        return True, " + ".join(parts)
    if IS_MACOS:
        return _mac(enable, GUARD_MAC_LABEL, ["/bin/bash", guard], log=log)
    root = config.ROOT
    return _linux(enable, GUARD_LINUX_DESKTOP, "Книжный страж (страж)",
                  f"bash -c \"cd '{root}' && bash run.sh guard\"", log=log)


def enable(log=print):
    return _app_autostart(True, log=log)


def disable(log=print):
    return _app_autostart(False, log=log)


def is_enabled() -> bool:
    if IS_WINDOWS:
        return (_win_enabled_schtasks(TASK_NAME)
                or _win_enabled_registry(REG_VALUE))
    if IS_MACOS:
        return _mac_enabled(MAC_LABEL)
    return _linux_enabled(LINUX_DESKTOP)


def describe() -> str:
    if not is_enabled():
        return "автозапуск выключен"
    if IS_WINDOWS:
        bits = []
        if _win_enabled_schtasks(TASK_NAME):
            bits.append(f"Планировщик «{TASK_NAME}»")
        if _win_enabled_registry(REG_VALUE):
            bits.append("автозагрузка Windows")
        return "включён (" + ", ".join(bits) + ")"
    if IS_MACOS:
        return f"включён (LaunchAgent {MAC_LABEL})"
    return f"включён ({_linux_desktop_path(LINUX_DESKTOP)})"


def enable_guard(log=print):
    return _guard_autostart(True, log=log)


def disable_guard(log=print):
    return _guard_autostart(False, log=log)


def guard_enabled() -> bool:
    if IS_WINDOWS:
        return (_win_enabled_schtasks(GUARD_TASK_NAME)
                or _win_enabled_registry(GUARD_REG_VALUE))
    if IS_MACOS:
        return _mac_enabled(GUARD_MAC_LABEL)
    return _linux_enabled(GUARD_LINUX_DESKTOP)


def guard_describe() -> str:
    if not guard_enabled():
        return "автозапуск стража выключен"
    if IS_WINDOWS:
        bits = []
        if _win_enabled_schtasks(GUARD_TASK_NAME):
            bits.append(f"Планировщик «{GUARD_TASK_NAME}»")
        if _win_enabled_registry(GUARD_REG_VALUE):
            bits.append("Run-ключ «BookGuardGuard»")
        return "включён (" + ", ".join(bits) + ")"
    if IS_MACOS:
        return f"включён (LaunchAgent {GUARD_MAC_LABEL})"
    return f"включён ({_linux_desktop_path(GUARD_LINUX_DESKTOP)})"


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

# -*- coding: utf-8 -*-
"""Блокировка и разблокировка Wi-Fi (Windows, Linux, macOS).

При отсутствии прав/системных утилит работает в режиме симуляции:
действия пишутся в журнал, сам Wi-Fi не трогается.
"""
import platform
import subprocess
import sys

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


def _run(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return False, str(e)


class WifiController:
    """block()/unblock() переключают Wi-Fi адаптер.

    simulate=True — ничего не менять в системе (для демо/тестов).
    """

    def __init__(self, simulate=False, log=print):
        self.simulate = simulate
        self.log = log
        self.last_error = None

    # ------------- внутренние команды по платформам
    def _iface_windows(self):
        ok, out = _run(["netsh", "interface", "show", "interface"])
        for line in out.splitlines():
            low = line.lower()
            if ("wi-fi" in low or "wireless" in low or "беспроводн" in low
                    or "wi fi" in low) and ("admin" not in low):
                parts = line.split()
                if len(parts) >= 4:
                    return " ".join(parts[3:])
        for candidate in ("Wi-Fi", "Беспроводная сеть", "Wireless Network Connection"):
            return candidate
        return "Wi-Fi"

    def _set_windows(self, enable: bool):
        name = self._iface_windows()
        state = "ENABLED" if enable else "DISABLED"
        ok, out = _run(["netsh", "interface", "set", "interface", name, f"admin={state}"])
        if not ok:
            ok, out = _run([
                "powershell", "-Command",
                f"Get-NetAdapter -Name '*Wi*','*wi*','*Беспровод*' | "
                f"{'Enable' if enable else 'Disable'}-NetAdapter -Confirm:$false",
            ])
        return ok, out

    def _set_linux(self, enable: bool):
        if enable:
            ok1, o1 = _run(["rfkill", "unblock", "wifi"])
            ok2, o2 = _run(["nmcli", "radio", "wifi", "on"])
        else:
            ok1, o1 = _run(["nmcli", "radio", "wifi", "off"])
            ok2, o2 = _run(["rfkill", "block", "wifi"])
        return ok1 or ok2, o1 + o2

    def _set_macos(self, enable: bool):
        ok, out = _run(["networksetup", "-listallhardwareports"])
        dev = "en0"
        lines = out.splitlines()
        for i, ln in enumerate(lines):
            if "Wi-Fi" in ln or "AirPort" in ln:
                if i + 1 < len(lines) and "Device:" in lines[i + 1]:
                    dev = lines[i + 1].split("Device:")[1].strip()
                break
        return _run(["networksetup", "-setairportpower", dev, "on" if enable else "off"])

    # ------------- публичный API
    def block(self):
        return self._apply(enable=False)

    def unblock(self):
        return self._apply(enable=True)

    def _apply(self, enable: bool):
        action = "ВКЛЮЧИТЬ" if enable else "ОТКЛЮЧИТЬ"
        if self.simulate:
            self.log(f"[симуляция] Wi-Fi {action.lower()} (без изменений в системе)")
            return True
        self.log(f"Wi-Fi: {action}...")
        if IS_WINDOWS:
            ok, out = self._set_windows(enable)
        elif IS_MACOS:
            ok, out = self._set_macos(enable)
        else:
            ok, out = self._set_linux(enable)
        if not ok:
            self.last_error = out.strip()[:400]
            self.log("Не удалось изменить Wi-Fi. Нужны права администратора.")
            self.log(f"({self.last_error})")
            self.log("Запустите программу от имени администратора / через sudo.")
        return ok

    def status_hint(self) -> str:
        if self.simulate:
            return "режим симуляции"
        if IS_WINDOWS:
            ok, out = _run(["netsh", "interface", "show", "interface"])
            for line in out.splitlines():
                low = line.lower()
                if "wi-fi" in low or "беспроводн" in low or "wireless" in low:
                    return line.strip()
            return "адаптер Wi-Fi"
        if IS_LINUX:
            ok, out = _run(["nmcli", "radio"])
            return out.strip() or "Wi-Fi"
        return "Wi-Fi"


def print_admin_help():
    if IS_WINDOWS:
        print('Запустите ярлык "Книжный страж (админ)" или install.bat с правами администратора.')
    elif IS_LINUX:
        print("Запустите: sudo python3 -m bookguard gui   (или настройте NOPASSWD для nmcli/rfkill)")
    else:
        print("Запустите программу через sudo, либо разрешите доступ в Системных настройках.")


if __name__ == "__main__":
    c = WifiController()
    if len(sys.argv) > 1 and sys.argv[1] == "unblock":
        c.unblock()
    else:
        c.block()

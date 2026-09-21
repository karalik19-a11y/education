# -*- coding: utf-8 -*-
"""Блокировка и разблокировка Wi-Fi (Windows, Linux, macOS).

Важно про время: включение Wi-Fi-адаптера (netsh admin=ENABLED /
Enable-NetAdapter) перезагружает драйвер — Windows может держать команду
от нескольких секунд до 2–3 минут. Поэтому:
  * у команд адаптера отдельный большой таймаут ADAPTER_OP_TIMEOUT —
    медленный netsh больше не убивается посреди операции (старый таймаут
    15 с порвал половину включений);
  * асинхронные block_async()/unblock_async() — GUI не «зависает», а
    показывает «ВКЛЮЧАЮ…/БЛОКИРУЮ…», а по готовности сообщает результат;
  * wait_enabled() проверяет, что адаптер после включения действительно
    поднялся (не только «команда отработала»).

При отсутствии прав/системных утилит работает в режиме симуляции:
действия пишутся в журнал, сам Wi-Fi не трогается.
"""
import platform
import subprocess
import sys
import threading
import time

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

ADAPTER_OP_TIMEOUT = 240  # сек. Драйверу Wi-Fi нужно время на переинициализацию.


def _run(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return False, f"таймаут команды ({timeout} с)"
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
        ok, out = _run(
            ["netsh", "interface", "set", "interface", name, f"admin={state}"],
            timeout=ADAPTER_OP_TIMEOUT,
        )
        if not ok:
            ok2, out2 = _run([
                "powershell", "-NoProfile", "-Command",
                f"Get-NetAdapter -Name '*Wi*','*wi*','*Беспровод*' | "
                f"{'Enable' if enable else 'Disable'}-NetAdapter -Confirm:$false",
            ], timeout=ADAPTER_OP_TIMEOUT)
            if ok2:
                return True, out2
            return False, (out + " | " + out2)
        return True, out

    def wifi_iface_state_windows(self):
        """Состояние Wi-Fi-интерфейса: (admin_state, op_state) или (None, None).

        Значения зависят от языка системы:
        admin: Enabled/Disabled (Включен/Отключен)
        op:    Connected/disconnected/Media disconnected (Подключен/...)
        """
        ok, out = _run(["netsh", "interface", "show", "interface"])
        if not ok:
            return None, None
        for line in out.splitlines():
            low = line.lower()
            if ("wi-fi" in low or "wireless" in low or "беспроводн" in low
                    or "wi fi" in low) and "admin" not in low:
                parts = line.split()
                if len(parts) >= 4:
                    return parts[0], parts[1]
        return None, None

    def wait_enabled(self, timeout=90):
        """Дождаться, пока адаптер реально включится.

        -> (ok: bool, state: str), где state — "connected" (сеть есть),
        "enabled" (адаптер работает, сеть может ещё подключаться) или
        "timeout" (не дождались).
        """
        if self.simulate or not IS_WINDOWS:
            return True, "connected"
        deadline = time.time() + timeout
        while time.time() < deadline:
            admin, op = self.wifi_iface_state_windows()
            a = (admin or "").lower()
            o = (op or "").lower()
            if a in ("enabled", "включен", "включён"):
                if "connected" in o or "подключ" in o:
                    return True, "connected"
                # адаптер включён — команды не ждать больше,
                # подхватывание сети идёт фоном у Windows
                return True, "enabled"
            time.sleep(1.5)
        admin, _op = self.wifi_iface_state_windows()
        a = (admin or "").lower()
        return a in ("enabled", "включен", "включён"), "timeout"

    def _set_linux(self, enable: bool):
        def try_cmds(cmds):
            out_all, ok_any = "", False
            for cmd in cmds:
                ok, out = _run(cmd)
                if not ok:
                    # попытка через sudo без запроса пароля (см. install.sh)
                    ok2, out2 = _run(["sudo", "-n"] + cmd)
                    ok, out = (ok2, out2) if ok2 else (ok, out + out2)
                ok_any = ok_any or ok
                out_all += out
            return ok_any, out_all

        if enable:
            return try_cmds([["rfkill", "unblock", "wifi"], ["nmcli", "radio", "wifi", "on"]])
        return try_cmds([["nmcli", "radio", "wifi", "off"], ["rfkill", "block", "wifi"]])

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

    # ------------- асинхронный API (для GUI — интерфейс не «зависает»)
    def _apply_async(self, enable: bool, on_done):
        def work():
            try:
                ok = self._apply(enable)
            except Exception as e:  # noqa: BLE001
                ok = False
                self.last_error = str(e)
            state = ""
            if ok and enable:
                # команда отработала — успех; дождаться лишь реальной
                # подхватки сети (чисто информационно для сообщения)
                _raised, state = self.wait_enabled()
                if state == "timeout":
                    state = "enabled"
            try:
                on_done(ok, state)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=work, daemon=True).start()

    def block_async(self, on_done):
        self._apply_async(False, on_done)

    def unblock_async(self, on_done):
        self._apply_async(True, on_done)

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

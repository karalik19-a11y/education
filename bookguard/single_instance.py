# -*- coding: utf-8 -*-
"""Защита от двойного запуска (один экземпляр приложения).

Ключевой файл data/.lock:  pid + время запуска + метка.
Если предыдущий процесс умер (или завис при закрытии), ключ «похищается» —
программа гарантированно запускается заново, в том числе после перезагрузки.
"""
import json
import os
import platform
import time
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        # ВАЖНО: на Windows os.kill(pid, 0) ЗАВЕРШАЕТ процесс — нельзя!
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:  # noqa: BLE001
        return False


def _cmdline_contains(pid: int, needle: str) -> bool:
    """Best-effort: проверка, что процесс с pid действительно наш."""
    if not IS_WINDOWS:
        try:
            data = Path(f"/proc/{pid}/cmdline").read_bytes()
            return needle.encode("utf-8", "ignore") in data
        except Exception:  # noqa: BLE001
            return True  # не смогли проверить — считаем «наш» (безопаснее для пользователя)
    return True


def acquire(lock_path: Path, label: str = "bookguard", ttl_hours: int = 24):
    """Попытаться занять экземпляр. -> (ok, info).

    ok=True  — программа может запускаться (мы заняли ключ).
    ok=False — уже работает другой экземпляр (info содержит подробности).
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if lock_path.exists():
        try:
            info = json.loads(lock_path.read_text(encoding="utf-8"))
            old_pid = int(info.get("pid", 0))
            age = now - float(info.get("ts", 0))
            if old_pid == os.getpid():
                return True, "наш собственный ключ (перезапуск в том же процессе)"
            if age < ttl_hours * 3600 and _pid_alive(old_pid) \
                    and _cmdline_contains(old_pid, label):
                return False, (
                    f"процесс {old_pid} запущен ({age / 60:.0f} мин назад)"
                )
        except Exception:  # noqa: BLE001
            pass  # битый ключ — похищаем
    try:
        tmp = lock_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"pid": os.getpid(), "ts": now, "label": label}),
            encoding="utf-8",
        )
        os.replace(tmp, lock_path)
        return True, "ключ занят"
    except Exception as e:  # noqa: BLE001
        # конкуренция за файл — попробуем ещё раз, иначе пускаем всё равно
        time.sleep(0.2)
        try:
            lock_path.write_text(
                json.dumps({"pid": os.getpid(), "ts": now, "label": label}),
                encoding="utf-8",
            )
            return True, "ключ занят (повтор)"
        except Exception:  # noqa: BLE001
            return True, f"ключ недоступен ({e}) — запуск продолжаем"


def release(lock_path: Path):
    try:
        info = json.loads(lock_path.read_text(encoding="utf-8"))
        if int(info.get("pid", -1)) == os.getpid():
            lock_path.unlink()
    except Exception:  # noqa: BLE001
        pass


def running_info(lock_path: Path):
    """Сведения о живом экземпляре (для сообщений). -> dict или None."""
    try:
        info = json.loads(Path(lock_path).read_text(encoding="utf-8"))
        pid = int(info.get("pid", 0))
        if pid and pid != os.getpid() and _pid_alive(pid):
            return info
    except Exception:  # noqa: BLE001
        pass
    return None

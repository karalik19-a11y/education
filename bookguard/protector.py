# -*- coding: utf-8 -*-
"""«Страж» — самообновляющаяся защита от удаления программы.

Как это работает:
  * установщик делает скрытую резервную копию программы в папке пользователя
    (Windows: %LOCALAPPDATA%\\BookGuard, Linux/macOS: ~/.local/share/bookguard и т.п.);
  * фоновый процесс-«страж» (без окна, в автозагрузке отдельным пунктом)
    каждые 2 секунды проверяет, что на месте ключевые файлы программы;
  * если программа удалили/переместили — страж молча ВОССТАНОВЛИВАЕТ её из
    резервной копии. Удаление невозможно, пока страж жив;
  * страж выключается ТОЛЬКО по паролю (67676767) — команда uninstall.
"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config, single_instance

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"

SALT = "bookguard-v2"


# ---------------------------------------------------------------------------
# Путь к «безопасной» директории (вне папки программы)
# ---------------------------------------------------------------------------
def backup_root() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "BookGuard"
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / "bookguard"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "bookguard"


def key_path() -> Path:
    return backup_root() / "guard.key"


def disabled_path() -> Path:
    return backup_root() / "guard.disabled"


def guard_pid_path() -> Path:
    return backup_root() / "guard.pid"


def guard_log_path() -> Path:
    return backup_root() / "guard.log"


def mirror_dir() -> Path:
    return backup_root() / "mirror"


# ---------------------------------------------------------------------------
# Пароль
# ---------------------------------------------------------------------------
def _hash_password(pw: str) -> str:
    return hashlib.sha256((SALT + pw).encode("utf-8")).hexdigest()


def ensure_key():
    """Записать хеш пароля (если ещё нет). Вызывается установщиком."""
    backup_root().mkdir(parents=True, exist_ok=True)
    if not key_path().exists():
        key_path().write_text(_hash_password(config.EMERGENCY_PASSWORD), encoding="utf-8")


def verify_password(pw: str) -> bool:
    """Проверка пароля: сначала хеш из безопасной папки, потом встроенный."""
    pw = (pw or "").strip()
    try:
        if key_path().exists():
            return key_path().read_text(encoding="utf-8").strip() == _hash_password(pw)
    except Exception:  # noqa: BLE001
        pass
    return pw == config.EMERGENCY_PASSWORD


def read_password_stdin(prompt: str) -> str:
    try:
        import getpass
        return getpass.getpass(prompt + " ")
    except Exception:  # noqa: BLE001
        line = sys.stdin.readline()
        return line.strip() if line else ""


# ---------------------------------------------------------------------------
# Резервная копия
# ---------------------------------------------------------------------------
def _mirror_ignores():
    """Что НЕ копировать: только .git (история не нужна). Остальное — всё."""
    skip_dirs = {".git", "download_tmp"}

    def ignore(_dir, names):
        return [n for n in names if n in skip_dirs]

    return ignore


def make_backup(root: Path = None, log=print) -> Path:
    """Создать/обновить резервную копию программы. Возвращает папку зеркала."""
    root = Path(root or config.ROOT)
    mirror = mirror_dir()
    backup_root().mkdir(parents=True, exist_ok=True)
    if not mirror.exists():
        mirror.mkdir(parents=True, exist_ok=True)

    # модели копируем, только если они не слишком большие (быстрая ~33 МБ — да)
    models_dir = root / "models"
    model_ok = False
    if models_dir.exists():
        size = sum(f.stat().st_size for f in models_dir.rglob("*") if f.is_file())
        model_ok = size < 400 * 1024 * 1024
        if not model_ok:
            log(f"[страж] модель {size // (1024 * 1024)} МБ не копируется "
                "(слишком большая) — при удалении скачается заново")

    # 1) всё, кроме .git и больших моделей
    shutil.copytree(root, mirror, dirs_exist_ok=True,
                    ignore=_mirror_ignores(), symlinks=False)
    if not model_ok:
        m = mirror / "models"
        if m.exists():
            shutil.rmtree(m, ignore_errors=True)

    # 2) данные (прогресс, база) — обязательно свежими
    data = root / "data"
    if data.exists():
        shutil.copytree(data, mirror / "data", dirs_exist_ok=True,
                        ignore=lambda d, n: [x for x in n if x in (".lock", ".lock.tmp")])
    mirror.joinpath("bg.marker").write_text(
        json.dumps({"ts": time.time(), "version": config.APP_VERSION}), encoding="utf-8")
    log(f"[страж] резервная копия готова: {mirror}")
    return mirror


def marker_paths(root: Path = None):
    """Ключевые файлы: если их нет — программа «удалена», пора восстанавливать."""
    root = Path(root or config.ROOT)
    p = [
        root / "run.bat",
        root / "run.sh",
        root / "run.py",
        root / "bookguard" / "__init__.py",
        root / "bookguard" / "app.py",
        root / "bookguard" / "stt.py",
        root / "data" / "library.db",
        root / "uninstall.bat",
        root / "uninstall.sh",
    ]
    # модель — проверяем, только если она реально есть в резервной копии
    # (не скачали — и «чинить» нечего)
    q = pick_current_quality()
    md = model_dir_any(q)
    mirror_model = mirror_dir() / "models" / md.name
    if mirror_model.is_dir() and any(mirror_model.rglob("*")):
        p.append(md)
    return p


def model_dir_any(quality: str) -> Path:
    from . import stt
    return stt.model_dir(quality)


def pick_current_quality() -> str:
    """Какое качество модели выбрано (из БД, если она жива)."""
    try:
        from .db import Library
        lib = Library()
        q = lib.get("stt_model", config.STT_MODEL_FAST)
        lib.close()
        return q
    except Exception:  # noqa: BLE001
        return config.STT_MODEL_FAST


def mirrors_ok() -> bool:
    return mirror_dir().exists() and (mirror_dir() / "bg.marker").exists()


def _copy2_best_effort(src, dst):
    """Копирование файла; если файл сейчас «в работе» (своё же python.exe /
    python, запущенный из .venv) и перезапись невозможна — пропускаем:
    файл всё равно на месте."""
    try:
        shutil.copy2(src, dst)
    except (PermissionError, OSError):
        if Path(dst).exists():
            return
        raise


def restore(root: Path = None, log=print):
    """Восстановить программу из зеркала (то, чего не хватает)."""
    root = Path(root or config.ROOT)
    mirror = mirror_dir()
    if not mirrors_ok():
        raise RuntimeError("нет резервной копии — восстановить нечего")
    root.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(mirror, root, dirs_exist_ok=True,
                        copy_function=_copy2_best_effort)
    except TypeError:  # очень старый Python без copy_function
        shutil.copytree(mirror, root, dirs_exist_ok=True)
    log(f"[страж] программа восстановлена: {root}")
    return root


# ---------------------------------------------------------------------------
# Включение / отключение защиты
# ---------------------------------------------------------------------------
def is_disabled() -> bool:
    return disabled_path().exists()


def disable_protection(reason: str = ""):
    """Выключить стража (только после верного пароля!)."""
    backup_root().mkdir(parents=True, exist_ok=True)
    disabled_path().write_text(
        json.dumps({"ts": time.time(), "reason": reason}), encoding="utf-8")


def cleanup_after_disable(log=print):
    """Убрать служебные файлы стража (вызывается после выключения)."""
    try:
        p = guard_pid_path()
        if p.exists():
            p.unlink()
    except Exception:  # noqa: BLE001
        pass


def kill_guard_process():
    try:
        p = guard_pid_path()
        if not p.exists():
            return
        pid = int(p.read_text(encoding="utf-8").strip() or 0)
        if pid and pid != os.getpid() and single_instance._pid_alive(pid):
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=15)
            else:
                os.kill(pid, 15)
                time.sleep(0.5)
                if single_instance._pid_alive(pid):
                    os.kill(pid, 9)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Фоновый процесс стража
# ---------------------------------------------------------------------------
def start_guard_background(log=print):
    """Запустить стража скрытым фоновым процессом (idempotent)."""
    try:
        if is_disabled():
            return False
        ok, info = single_instance.acquire(guard_pid_path(), label="bookguard-guard")
        if not ok:
            return False  # уже работает
        if IS_WINDOWS:
            python = Path(sys.executable)
            if python.name.lower() in ("pythonw.exe",) or os.environ.get("BG_INTERNAL"):
                target = [str(python), "-m", "bookguard", "guard"]
            else:
                pw = python.with_name("pythonw.exe")
                target = [str(pw if pw.exists() else python), "-m", "bookguard", "guard"]
            env = dict(os.environ, BG_INTERNAL="1")
            flags = 0x08000000  # CREATE_NO_WINDOW
            subprocess.Popen(target, cwd=str(config.ROOT), env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=flags, close_fds=True)
        else:
            subprocess.Popen(
                [sys.executable, "-m", "bookguard", "guard"],
                cwd=str(config.ROOT),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
            )
        single_instance.release(guard_pid_path())  # сам страж заново займёт ключ
        log("[страж] фоновый процесс запущен")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"[страж] не удалось запустить фоновый процесс: {e}")
        return False


def guard_loop(stop_event: threading.Event = None, interval: float = 2.0,
               root: Path = None, log=None) -> int:
    """Тело стража: проверка целостности и восстановление. Возвращает код выхода."""
    log = log or (lambda s: None)
    root = Path(root or config.ROOT)
    stop_event = stop_event or threading.Event()
    ensure_key()
    ok, info = single_instance.acquire(guard_pid_path(), label="bookguard-guard")
    if not ok:
        log(f"[страж] уже работает другой страж ({info}) — ухожу")
        return 0
    try:
        guard_pid_path().write_text(str(os.getpid()), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    if not mirrors_ok():
        # зеркала нет — либо не настроена защита, либо выполнено удаление:
        # НИКОГДА не воссоздаём копию сами (иначе «страж» воскреснет после
        # полного удаления)
        log("[страж] резервной копии нет — защита не активна, ухожу")
        return 0

    _last_restore = 0.0
    while not stop_event.is_set():
        try:
            if is_disabled() or not mirrors_ok():
                reason = "защита отключена по паролю" if is_disabled() \
                    else "резервная копия удалена (выполнено удаление)"
                log(f"[страж] {reason} — ухожу")
                from . import autostart
                autostart.disable_guard(log=log)
                cleanup_after_disable()
                break
            missing = [p for p in marker_paths(root)
                       if (p.is_dir() and not any(p.rglob("*"))) or not p.exists()]
            if missing:
                now = time.time()
                if now - _last_restore < 5:  # защита от «молотовидального» цикла
                    stop_event.wait(interval)
                    continue
                log("[страж] обнаружено удаление, восстанавливаю: "
                    + ", ".join(str(Path(m).name) for m in missing))
                try:
                    restore(root, log=log)
                    _last_restore = now
                    # после восстановления данных — обновляем зеркало свежими данными
                    try:
                        make_backup(root, log=log)
                    except Exception:  # noqa: BLE001
                        pass
                except Exception as e:  # noqa: BLE001
                    log(f"[страж] ошибка восстановления: {e}")
        except Exception as e:  # noqa: BLE001
            log(f"[страж] ошибка цикла: {e}")
        stop_event.wait(interval)
    return 0


def guard_main():
    log_path = guard_log_path()
    backup_root().mkdir(parents=True, exist_ok=True)

    def log(s):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {s}"
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass
        print(line, flush=True)

    if is_disabled():
        log("[страж] защита уже отключена — выхожу")
        return 0
    ensure_key()
    return guard_loop(log=log)


def spawn_final_wipe(root: Path = None):
    """Отложенное удаление папки программы отдельным фоновым процессом.

    Невозможно удалить папку «изнутри» (собственный процесс/скрипт в ней
    живёт), поэтому удаление делает независимый процесс через 3 секунды.
    """
    root = Path(root or config.ROOT)
    if IS_WINDOWS:
        tmp = config.DATA_DIR / "bg_wipe.cmd"
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                "@echo off\r\n"
                f"timeout /t 3 /nobreak >nul\r\n"
                f'rmdir /s /q "{root}".\r\n'
                f"timeout /t 2 /nobreak >nul\r\n"
                f'rmdir /s /q "{root}".\r\n'
                f'del "%~f0" >nul 2>nul\r\n', encoding="utf-8")
            import subprocess
            subprocess.Popen(["cmd", "/c", str(tmp)],
                             creationflags=0x08000000,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            pass
    else:
        tmp = config.DATA_DIR / "bg_wipe.sh"
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                "#!/bin/sh\nsleep 3\n"
                f"rm -rf {str(root)!r}\n"
                "sleep 2\n"
                f"rm -rf {str(root)!r}\n"
                f"rm -f {str(tmp)!r}\n", encoding="utf-8")
            import subprocess
            subprocess.Popen(["/bin/sh", str(tmp)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Полное удаление (по паролю)
# ---------------------------------------------------------------------------
def run_uninstall(password: str, delete_files: bool = True,
                  root: Path = None, log=print):
    """Полный порядок удаления. Пароль должен быть уже проверен вызывающим,
    но проверяем и здесь (двойной замок)."""
    root = Path(root or config.ROOT)
    if not verify_password(password):
        log("✗ Неверный пароль. Удаление запрещено.")
        return False
    log("Пароль принят. Начинаю полное удаление...")

    from . import autostart

    # 1) выключение защиты
    disable_protection("uninstall")
    kill_guard_process()

    # 2) остановка главного приложения
    try:
        lock = root / "data" / ".lock"
        if lock.exists():
            info = json.loads(lock.read_text(encoding="utf-8"))
            pid = int(info.get("pid", 0))
            if pid and pid != os.getpid() and single_instance._pid_alive(pid):
                if IS_WINDOWS:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, timeout=15)
                else:
                    os.kill(pid, 15)
                time.sleep(0.5)
    except Exception:  # noqa: BLE001
        pass

    # 3) автозапуск (приложение + страж)
    try:
        autostart.disable(log=log)
        autostart.disable_guard(log=log)
    except Exception as e:  # noqa: BLE001
        log(f"(автозапуск: {e})")

    # 4) ярлыки на рабочем столе
    desktop = Path.home() / "Desktop"
    for name in ("Книжный страж.lnk", "Книжный страж (админ).lnk",
                 "Книжный страж.desktop", "bookguard.desktop"):
        for cand in (desktop / name,
                     Path.home() / "Desktop" / name):
            try:
                if cand.exists():
                    cand.unlink()
                    log(f"удалён ярлык: {name}")
            except Exception:  # noqa: BLE001
                pass
    # Linux: рабочий стол может быть локализован
    for d in (Path.home() / "Рабочий стол", Path.home() / "Escritorio"):
        try:
            if d.exists():
                for f in d.glob("*.desktop"):
                    if "страж" in f.name.lower() or "bookguard" in f.name.lower():
                        f.unlink()
        except Exception:  # noqa: BLE001
            pass

    # 5) резервная копия и служебные файлы стража
    try:
        shutil.rmtree(backup_root(), ignore_errors=True)
        log("резервная копия и служебные файлы стража удалены")
    except Exception:  # noqa: BLE001
        pass
    # страховка: если страж всё ещё жив — останавливаем (без зеркала он и сам
    # закончится на следующей итерации)
    kill_guard_process()

    # 6) сами файлы программы
    if delete_files:
        try:
            os.chdir(str(root.parent))  # нельзя удалить свою рабочую папку
            shutil.rmtree(root, ignore_errors=True)
            log(f"папка программы удалена: {root}")
        except Exception as e:  # noqa: BLE001
            log(f"не удалось удалить папку программы: {e}")
    return True


# ---------------------------------------------------------------------------
# Настройка при установке
# ---------------------------------------------------------------------------
def setup(log=print):
    """Вызывается установщиком: хеш пароля + зеркало + автозапуск стража."""
    ensure_key()
    make_backup(log=log)
    from . import autostart
    ok, info = autostart.enable_guard(log=log)
    log(f"[страж] автозапуск стража: {'включён — ' + info if ok else 'не включён: ' + info}")
    start_guard_background(log=log)
    return True

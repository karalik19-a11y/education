# -*- coding: utf-8 -*-
"""Распознавание речи (Vosk, офлайн) — «живой» вариант без задержек.

Ключевые отличия от старого кода:
  * модель загружается ОДИН раз в фоне при старте приложения
    (старый код грузил её при каждом нажатии «Начать» — пауза в секунды);
  * слова засчитываются НА ЛЕТУ по «промежуточным» результатам
    (PartialResult), а не только после паузы в речи — прогресс идёт
    сразу, без ожидания тишины;
  * короткие блоки аудио (0.25 с) → живая расшифровка обновляется ~4 раза/с;
  * уровень звука с микрофона (GUI показывает, что микрофон работает);
  * защита от «затора» распознавания (отстаёт от реального времени):
    лишний аудиоблок отбрасывается, а не копится в очереди.
"""
import json
import os
import queue
import shutil
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import config

IS_WINDOWS = __import__("platform").system() == "Windows"


# ---------------------------------------------------------------------------
# Чистая логика: превращаем поток partial/final результатов в подтверждённые
# слова. Отдельный класс — чтобы её можно было проверять тестами без vosk.
# ---------------------------------------------------------------------------
class PartialConsumer:
    """Держит «хвост» текущей фразы и выдаёт только стабилизировавшиеся слова.

    Последнее слово текущей фразы считается «неустойчивым» (распознаватель
    ещё может его переписать) и в on_words не попадает — оно подтвердится
    следующим блоком (задержка ~0.25–0.5 с, вместо прежних «до тишины»).
    """

    def __init__(self):
        self.spoken = []        # все стабилизировавшиеся слова (общий поток)
        self.fed = 0            # сколько из spoken уже выдано наружу
        self._prev_partial = []  # слова последнего partial
        self.live = ""          # текст текущего partial (для отображения)

    # -- внутреннее ---------------------------------------------------------
    def _overlap(self, w: list, limit: int) -> int:
        """Максимальное k (<= limit): w[:k] совпадает с хвостом spoken."""
        for k in range(min(len(w), limit, len(self.spoken)), -1, -1):
            if k == 0 or w[:k] == self.spoken[-k:]:
                return k
        return 0

    def _absorb(self, w: list, final: bool):
        prev = self._prev_partial
        p = len(prev)
        if p and (final or len(w) < p):
            # final либо «убравшийся» partial — хвост spoken перестраиваем
            k = self._overlap(w, p)
            if k < p:
                self.spoken = self.spoken[: len(self.spoken) - (p - k)]
        else:
            k = self._overlap(w, p)
        self.spoken.extend(w[k:])
        self._prev_partial = [] if final else w

        if final:
            confirmed = self.spoken[self.fed:]
            self.fed = len(self.spoken)
        else:
            upper = max(self.fed, len(self.spoken) - 1)
            confirmed = self.spoken[self.fed:upper]
            self.fed = upper
        return confirmed

    # -- API -----------------------------------------------------------------
    def on_partial(self, text: str) -> list:
        """Новый partial. Возвращает подтвердившиеся слова (можно [])."""
        text = (text or "").strip()
        self.live = text
        w = text.split()
        return self._absorb(w, final=False)

    def on_final(self, words) -> list:
        """Итог фразы (Result). Возвращает подтвердившиеся слова."""
        w = [x for x in (words or []) if str(x).strip()]
        if not w:
            w = self.live.split()
        self.live = ""
        return self._absorb(w, final=True)


# ---------------------------------------------------------------------------
# Модель
# ---------------------------------------------------------------------------
def model_dir(quality: str) -> Path:
    return config.VOSK_MODEL_BIG_DIR if quality == config.STT_MODEL_BIG \
        else config.VOSK_MODEL_FAST_DIR


def model_quality_name(quality: str) -> str:
    return "максимальная точность" if quality == config.STT_MODEL_BIG else "быстрая"


def has_model(quality: str) -> bool:
    d = model_dir(quality)
    return d.is_dir() and any(d.rglob("*"))


def pick_quality(prefer: str) -> str:
    """Из предпочитаемого качества — то, что реально установлено."""
    if has_model(prefer):
        return prefer
    for q in (config.STT_MODEL_FAST, config.STT_MODEL_BIG):
        if q != prefer and has_model(q):
            return q
    return prefer


class ModelManager:
    """Фоновая одноразовая загрузка модели Vosk."""

    _lock = threading.Lock()
    _model = None
    _quality = None
    _loading = False
    _ready = False
    _error = ""

    @classmethod
    def state(cls):
        return {
            "ready": cls._ready,
            "loading": cls._loading,
            "quality": cls._quality,
            "error": cls._error,
        }

    @classmethod
    def get_model(cls):
        return cls._model

    @classmethod
    def ensure(cls, quality: str, on_state=None):
        """Загрузить модель в фоне, если ещё не загружена."""
        with cls._lock:
            if cls._ready or cls._loading:
                return
            if not has_model(quality):
                cls._error = f"не найдена модель: {model_dir(quality)}"
                if on_state:
                    on_state("missing", cls._error)
                return
            cls._loading = True
            cls._quality = quality
            cls._error = ""

        def load():
            try:
                import vosk
                vosk.SetLogLevel(-1)
                m = vosk.Model(str(model_dir(quality)))
                with cls._lock:
                    cls._model = m
                    cls._ready = True
                    cls._loading = False
                if on_state:
                    on_state("ready", quality)
            except Exception as e:  # noqa: BLE001
                with cls._lock:
                    cls._loading = False
                    cls._error = str(e)
                if on_state:
                    on_state("error", str(e))

        threading.Thread(target=load, daemon=True).start()

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._model = None
            cls._ready = False
            cls._loading = False
            cls._error = ""
            cls._quality = None


# ---------------------------------------------------------------------------
# Скачивание моделей
# ---------------------------------------------------------------------------
def _download(url: str, dest: Path, progress_cb):
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
    tmp.rename(dest)


def download_model(quality: str, progress_cb=None, log=print) -> Path:
    """Скачать и распаковать модель. Возвращает папку модели. (в отдельном потоке!)"""
    dest = model_dir(quality)
    tmp_dir = dest.parent / "download_tmp"
    zip_path = dest.parent / (f"vosk-{quality}.zip")

    def try_extract(folder_names):
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_dir)
        for name in folder_names:
            found = tmp_dir / name
            if found.is_dir():
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.move(str(found), str(dest))
                return True
            if found.is_file():  # может лежать вложенной
                parent = found.parent
                if parent.is_dir():
                    if dest.exists():
                        shutil.rmtree(dest, ignore_errors=True)
                    shutil.move(str(parent), str(dest))
                    return True
        return False

    try:
        if quality == config.STT_MODEL_BIG:
            log("Скачивание модели «максимальная точность» (~1.4 ГБ)...")
            _download(config.VOSK_MODEL_BIG_URL, zip_path, progress_cb)
            if not try_extract(["vosk-model-ru-0.42", "vosk-model-ru-big"]):
                raise RuntimeError("в архиве не найдена папка модели")
        else:
            log("Скачивание быстрой модели (~33 МБ)...")
            try:
                _download(config.VOSK_MODEL_FAST_URL, zip_path, progress_cb)
                ok = try_extract(["vosk-model-small-ru-0.22", "vosk-model-small-ru"])
            except Exception as e:  # noqa: BLE001
                log(f"alphacephei.com недоступен ({e}) — пробую зеркало GitHub...")
                zip_path = dest.parent / "vosk-mirror.zip"
                _download(config.VOSK_MODEL_FAST_MIRROR, zip_path, progress_cb)
                ok = try_extract(["vosk-model-small-ru"])
                if not ok:
                    # в зеркале папка вложена в корень репозитория
                    ok = try_extract([tmp_dir.name + "-main/vosk-model-small-ru"])
            if not ok:
                raise RuntimeError("в архиве не найдена папка модели")
        if not has_model(quality):
            raise RuntimeError("модель распаковалась, но папка пуста")
        log(f"Модель готова: {dest}")
        return dest
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except TypeError:
            try:
                zip_path.unlink()
            except FileNotFoundError:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)


def list_input_devices():
    try:
        import sounddevice as sd
        return [f"{i}: {d['name']}" for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0]
    except Exception as e:  # noqa: BLE001
        return [f"(нет доступа к звуковым устройствам: {e})"]


# ---------------------------------------------------------------------------
# Распознаватель с микрофона
# ---------------------------------------------------------------------------
class VoskRecognizer:
    """Vosk + sounddevice. Колбэки:

    on_words(list[str])  — подтверждённые слова (и засчитывать их в прогресс);
    on_partial(str)      — живой текст текущей фразы (для «большой» строки);
    on_level(float 0..1) — уровень звука (микрофон «живой»);
    on_state(str, msg)   — служебное состояние (затор, ошибки).
    """
    name = "Vosk (микрофон, офлайн)"

    def __init__(self, on_words=None, on_partial=None, on_level=None,
                 on_state=None, model=None, device=None,
                 blocksize=config.BLOCK_SIZE):
        self.on_words = on_words or (lambda w: None)
        self.on_partial = on_partial or (lambda s: None)
        self.on_level = on_level or (lambda v: None)
        self.on_state = on_state or (lambda s, m="": None)
        self.model = model
        self.device = device
        self.blocksize = blocksize
        self._stop = threading.Event()
        self._q = queue.Queue(maxsize=80)
        self.thread = None
        self.stream = None
        self.rec = None
        self.dropped = 0
        self.consumer = PartialConsumer()

    # -- запуск --------------------------------------------------------------
    def start(self):
        try:
            import vosk
            import sounddevice as sd
        except Exception as e:  # noqa: BLE001 (ImportError / OSError PortAudio и пр.)
            raise RuntimeError(
                f"Не удалось подключить распознавание: {e}\n"
                "Запустите install.sh или install.bat — всё установится автоматически."
            )
        model = self.model
        if model is None:
            if not has_model(config.STT_MODEL_FAST) and not has_model(config.STT_MODEL_BIG):
                raise RuntimeError(
                    "Не найдена модель распознавания речи.\n"
                    "Запустите install.sh / install.bat или скачайте модель в «Настройках»."
                )
            q = pick_quality(config.STT_MODEL_FAST)
            self.on_state("loading", f"Загрузка модели ({model_quality_name(q)})...")
            model = vosk.Model(str(model_dir(q)))
            self.on_state("ready", "")
        self._sd = sd
        self._vosk = vosk
        self.rec = vosk.KaldiRecognizer(model, config.SAMPLE_RATE)
        self.consumer = PartialConsumer()

        def callback(indata, frames, t, status):  # noqa: WPS437
            try:
                if self._q.full():
                    try:
                        self._q.get_nowait()
                        self.dropped += 1
                        self.on_state("drop", "компьютер не успевает — часть речи пропущена")
                    except queue.Empty:
                        pass
                self._q.put_nowait(bytes(indata))
                self._level(indata)
            except Exception:  # noqa: BLE001
                pass

        self.stream = sd.RawInputStream(
            samplerate=config.SAMPLE_RATE, blocksize=self.blocksize, dtype="int16",
            channels=1, device=self.device, callback=callback,
        )
        self.stream.start()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _level(self, indata):
        n = len(indata) // 2
        if n < 8:
            return
        step = max(1, n // 160)
        s = 0
        cnt = 0
        b = indata
        for i in range(0, n * 2 - 1, step * 2):
            v = int.from_bytes(b[i:i + 2], "little", signed=True)
            s += v * v
            cnt += 1
        rms = (s / max(1, cnt)) ** 0.5 / 32768.0
        try:
            self.on_level(min(1.0, rms * 4.0))
        except Exception:  # noqa: BLE001
            pass

    # -- обработка аудио -------------------------------------------------------
    def _process(self, data: bytes):
        if self.rec.AcceptWaveform(data):
            res = json.loads(self.rec.Result())
            words = [w.get("word", "") for w in res.get("result", [])]
            words = [w for w in words if w]
            if not words and res.get("text"):
                words = str(res["text"]).split()
            new = self.consumer.on_final(words)
            self.on_partial("")
            if new:
                self.on_words(new)
        else:
            part = json.loads(self.rec.PartialResult())
            p = part.get("partial", "")
            new = self.consumer.on_partial(p)
            if p:
                self.on_partial(p)
            if new:
                self.on_words(new)

    def _run(self):
        while not self._stop.is_set():
            try:
                data = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            if self._stop.is_set():
                break
            try:
                self._process(data)
            except Exception as e:  # noqa: BLE001
                self.on_state("error", f"ошибка распознавания: {e}")

    def feed_audio(self, data: bytes):
        """Прямая подача аудио (для тестов без микрофона)."""
        self._process(data)

    def stop(self):
        self._stop.set()
        stream = self.stream
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        if self.thread:
            self.thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Подстановочный распознаватель (демо): имитирует живую речь
# ---------------------------------------------------------------------------
class FakeRecognizer:
    """«Услышанные» слова подаются из текста книги с реалистичными паузами
    и частичными результатами — интерфейс работает точно как с микрофоном."""
    name = "демо (без микрофона)"

    def __init__(self, words, words_per_second=150, noise_every=25,
                 on_words=None, on_partial=None, on_level=None, on_state=None):
        self.words = list(words)
        self.wps = words_per_second
        self.noise_every = noise_every
        self.on_words = on_words or (lambda w: None)
        self.on_partial = on_partial or (lambda s: None)
        self.on_level = on_level or (lambda v: None)
        self.on_state = on_state or (lambda s, m="": None)
        self._stop = threading.Event()
        self.thread = None
        self.dropped = 0

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        i = 0
        utter = []
        n_words = len(self.words)
        while i < n_words and not self._stop.is_set():
            # «говорим» слово
            w = self.words[i]
            i += 1
            utter.append(w)
            if len(utter) % self.noise_every == 0:
                utter.append("эээ")
            # внутри фразы — partial растёт
            self.on_partial(" ".join(utter))
            self.on_level(0.35 + (i % 7) * 0.06)
            time.sleep(1.0 / self.wps * 2)  # пара слов за шаг
            # «пауза между фразами» — final
            if len(utter) >= 6 or i >= n_words:
                final_words = list(utter)
                utter = []
                new = list(final_words)
                self.on_partial("")
                self.on_level(0.02)
                self.on_words(new)
                time.sleep(1.0 / self.wps)

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=2)

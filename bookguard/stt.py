# -*- coding: utf-8 -*-
"""Распознавание речи с микрофона (Vosk, офлайн) и подстановочный режим для тестов.

Ключевая идея «живого» режима: слова из *частичных* гипотез Vosk (partial)
сразу уходят в матчер книги — подсветка идёт в реальном времени, без пауз.
Финальные результаты (final) лишь догружают «хвост» фразы, дубликаты
отсекаются по общему префиксу уже отданных слов.
"""
import json
import math
import queue
import struct
import threading
import time

from . import config


def _common_prefix_len(a, b):
    """Длина общего префикса двух списков слов."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _rms_level(raw_bytes):
    """Громкость аудиоблока 0..1 (для индикатора микрофона)."""
    if not raw_bytes:
        return 0.0
    n = len(raw_bytes) // 2
    if n <= 0:
        return 0.0
    fmt = "<%dh" % n
    try:
        samples = struct.unpack(fmt, raw_bytes[:n * 2])
    except struct.error:
        return 0.0
    acc = 0.0
    for s in samples:
        acc += s * s
    rms = math.sqrt(acc / n) / 32768.0
    # лёгкая компрессия, чтобы тихая речь тоже была видна
    return max(0.0, min(1.0, rms * 3.0))


class FakeRecognizer:
    """Режим тренировки/демо: «услышанные» слова подаются текстом.

    words_per_second=None — мгновенно (для selftest/simulate).
    """

    name = "подстановочный (без микрофона)"

    def __init__(self, words, words_per_second=None, noise_every=0, on_words=None,
                 on_partial=None, on_level=None):
        self.words = list(words)
        self.wps = words_per_second
        self.noise_every = noise_every
        self.on_words = on_words or (lambda words: None)
        self.on_partial = on_partial or (lambda s: None)
        self.on_level = on_level or (lambda v: None)
        self._stop = threading.Event()
        self.thread = None

    def start(self):
        def run():
            i = 0
            n = 0
            # маленькими порциями — как живая речь, чтобы подсветка шла плавно
            step = 1 if self.wps and self.wps >= 60 else 3
            while i < len(self.words) and not self._stop.is_set():
                chunk = self.words[i:i + step]
                i += step
                n += 1
                if self.noise_every and n % self.noise_every == 0:
                    chunk = list(chunk) + ["эээ"]
                try:
                    self.on_partial(" ".join(chunk) + " …")
                    self.on_words(chunk)
                    self.on_level(0.35 + 0.3 * ((n * 37) % 10) / 10.0)
                except Exception:  # noqa: BLE001
                    break
                if self.wps:
                    time.sleep(step / float(self.wps))
            try:
                self.on_partial("")
                self.on_level(0.0)
            except Exception:  # noqa: BLE001
                pass
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=2)

    def feed_now(self, words):
        self.on_words(list(words))


class VoskRecognizer:
    """Офлайн-распознавание с микрофона через Vosk + sounddevice.

    Слова отдаются в реальном времени: сначала из частичных гипотез
    (кроме нестабильного «хвоста»), затем финальный результат
    догружает остаток фразы без дублей.
    """
    name = "Vosk (микрофон, офлайн)"

    # сколько последних слов partial-гипотезы считаем нестабильными
    UNSTABLE_TAIL = 1

    def __init__(self, on_words=None, on_partial=None, on_level=None,
                 on_error=None, model_dir=None, device=None):
        self.on_words = on_words or (lambda w: None)
        self.on_partial = on_partial or (lambda s: None)
        self.on_level = on_level or (lambda v: None)
        self.on_error = on_error or (lambda e: None)
        self.model_dir = model_dir or config.VOSK_MODEL_DIR
        self.device = device
        self._stop = threading.Event()
        self._q = queue.Queue()
        self.thread = None
        self.error = None
        # слова текущей фразы, уже отданные в матчер (для отсечения дублей)
        self._fed = []
        self._last_partial = ""
        self._last_level_at = 0.0

    # ------------------------------------------------------------ управление
    def start(self):
        try:
            import vosk  # noqa: WPS433
            import sounddevice as sd  # noqa: WPS433
        except ImportError as e:
            self.error = (
                f"Не установлены модули vosk/sounddevice ({e}).\n"
                "Запустите install.sh или install.bat — всё установится автоматически."
            )
            raise RuntimeError(self.error)
        if not self.model_dir.exists():
            self.error = (
                f"Не найдена модель распознавания речи: {self.model_dir}\n"
                "Запустите install.sh (или install.bat) — модель скачается автоматически."
            )
            raise RuntimeError(self.error)

        vosk.SetLogLevel(-1)
        self._model = vosk.Model(str(self.model_dir))
        self._rec = vosk.KaldiRecognizer(self._model, config.SAMPLE_RATE)
        try:
            self._rec.SetWords(True)
        except Exception:  # noqa: BLE001
            pass
        try:
            # пословные частичные гипотезы — точнее и чаще обновления
            self._rec.SetPartialWords(True)
        except Exception:  # noqa: BLE001
            pass
        self._sd = sd

        def callback(indata, frames, t, status):
            try:
                self._q.put_nowait(bytes(indata))
            except queue.Full:
                pass
            except Exception:  # noqa: BLE001
                pass

        try:
            self._stream = sd.RawInputStream(
                samplerate=config.SAMPLE_RATE, blocksize=4000, dtype="int16",
                channels=1, device=self.device, callback=callback,
            )
            self._stream.start()
        except Exception as e:  # noqa: BLE001
            self.error = (
                f"Не удалось открыть микрофон ({e}).\n"
                "Проверьте, что микрофон подключён и разрешён для программы,\n"
                "и выберите другой микрофон в списке, если их несколько."
            )
            raise RuntimeError(self.error)
        self._stop.clear()
        self._fed = []
        self._last_partial = ""
        # выбросить stale-аудио, оставшееся в очереди от прошлого запуска
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        if self.thread:
            self.thread.join(timeout=3)
        # догрузить «хвост» последней фразы из финального результата
        try:
            if getattr(self, "_rec", None) is not None:
                import json as _json
                res = _json.loads(self._rec.FinalResult() or "{}")
                words = [w.get("word", "") for w in res.get("result", []) if w.get("word")]
                if not words and res.get("text"):
                    words = str(res["text"]).split()
                self._deliver_final(words)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.on_partial("")
            self.on_level(0.0)
        except Exception:  # noqa: BLE001
            pass

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    # ------------------------------------------------------------ аудиопоток
    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    data = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                self._push_level(data)
                try:
                    if self._rec.AcceptWaveform(data):
                        res = json.loads(self._rec.Result() or "{}")
                        words = [w.get("word", "") for w in res.get("result", [])
                                 if w.get("word")]
                        if not words and res.get("text"):
                            words = str(res["text"]).split()
                        self._deliver_final(words)
                        self._emit_partial("")
                    else:
                        part = json.loads(self._rec.PartialResult() or "{}")
                        text = str(part.get("partial", "") or "")
                        words = self._partial_words(part, text)
                        self._deliver_partial(words)
                        self._emit_partial(text)
                except Exception as e:  # noqa: BLE001
                    # одиночный сбой кадра не должен ронять весь поток
                    try:
                        self.on_error(f"сбой кадра распознавания: {e}")
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            try:
                self.on_error(str(e))
            except Exception:  # noqa: BLE001
                pass

    def _partial_words(self, part, text):
        words = []
        try:
            for w in part.get("partial_result", []) or []:
                if isinstance(w, dict) and w.get("word"):
                    words.append(str(w["word"]))
        except Exception:  # noqa: BLE001
            words = []
        if not words and text:
            words = text.split()
        return words

    def _emit_partial(self, text):
        if text != self._last_partial:
            self._last_partial = text
            try:
                self.on_partial(text)
            except Exception:  # noqa: BLE001
                pass

    def _push_level(self, data):
        now = time.monotonic()
        if now - self._last_level_at < 0.09:  # ~11 раз в секунду
            return
        self._last_level_at = now
        try:
            self.on_level(_rms_level(data))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ выдача слов
    def _deliver_partial(self, words):
        """Отдать новые стабильные слова из частичной гипотезы."""
        words = [w for w in (words or []) if w]
        if not words:
            return
        fed = self._fed
        k = _common_prefix_len(fed, words)
        if k < len(fed):
            # гипотеза «переписала» уже отданное — ждём финальный результат,
            # чтобы не плодить дубли
            return
        stable_end = len(words) - self.UNSTABLE_TAIL
        if stable_end <= len(fed):
            return  # пока только нестабильный хвост — рано
        new_words = words[len(fed):stable_end]
        if new_words:
            fed.extend(new_words)
            try:
                self.on_words(list(new_words))
            except Exception:  # noqa: BLE001
                pass

    def _deliver_final(self, words):
        """Финальный результат: догрузить остаток фразы без дублей."""
        words = [str(w) for w in (words or []) if w]
        fed = self._fed
        if words:
            k = _common_prefix_len(fed, words)
            if k >= len(fed):
                new_words = words[k:]
            elif k >= max(0, len(fed) - 2) and k > 0:
                # лёгкая правка конца фразы — догружаем хвост
                new_words = words[k:]
            else:
                # гипотеза сильно пересмотрена — отдаём фразу целиком,
                # матчер сам отсечёт повторы окном поиска
                new_words = words
            if new_words:
                try:
                    self.on_words(list(new_words))
                except Exception:  # noqa: BLE001
                    pass
        self._fed = []


def input_devices():
    """[(index, name)] микрофонов в системе (для выпадающего списка)."""
    try:
        import sounddevice as sd
        out = []
        for i, d in enumerate(sd.query_devices()):
            try:
                if d["max_input_channels"] > 0:
                    out.append((i, str(d["name"])))
            except Exception:  # noqa: BLE001
                continue
        return out
    except Exception as e:  # noqa: BLE001
        return [(-1, f"(нет доступа к звуковым устройствам: {e})")]


def list_input_devices():
    return [f"{i}: {name}" for i, name in input_devices()]


def has_vosk_model():
    return config.VOSK_MODEL_DIR.exists() and any(config.VOSK_MODEL_DIR.rglob("*"))

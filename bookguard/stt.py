# -*- coding: utf-8 -*-
"""Распознавание речи с микрофона (Vosk, офлайн) и подстановочный режим для тестов."""
import json
import os
import queue
import sys
import threading
import time

from . import config


class FakeRecognizer:
    """Режим тренировки/демо: «услышанные» слова подаются текстом.

    words_per_second=None — мгновенно (для selftest/simulate).
    """

    name = "подстановочный (без микрофона)"

    def __init__(self, words, words_per_second=None, noise_every=0, on_words=None):
        self.words = list(words)
        self.wps = words_per_second
        self.noise_every = noise_every
        self.on_words = on_words or (lambda words: None)
        self._stop = threading.Event()
        self.thread = None

    def start(self):
        def run():
            i = 0
            n = 0
            while i < len(self.words) and not self._stop.is_set():
                chunk = self.words[i:i + 3]
                i += 3
                n += 1
                if self.noise_every and n % self.noise_every == 0:
                    chunk = list(chunk) + ["эээ", "ммм"]
                self.on_words(chunk)
                if self.wps:
                    time.sleep(3.0 / self.wps)
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=2)

    def feed_now(self, words):
        self.on_words(list(words))


class VoskRecognizer:
    """Офлайн-распознавание с микрофона через Vosk + sounddevice."""
    name = "Vosk (микрофон, офлайн)"

    def __init__(self, on_words=None, on_partial=None, model_dir=None, device=None):
        self.on_words = on_words or (lambda w: None)
        self.on_partial = on_partial or (lambda s: None)
        self.model_dir = model_dir or config.VOSK_MODEL_DIR
        self.device = device
        self._stop = threading.Event()
        self._q = queue.Queue()
        self.thread = None
        self.error = None

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
        self._sd = sd

        def callback(indata, frames, t, status):
            self._q.put(bytes(indata))

        self._stream = sd.RawInputStream(
            samplerate=config.SAMPLE_RATE, blocksize=8000, dtype="int16",
            channels=1, device=self.device, callback=callback,
        )
        self._stream.start()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        import vosk
        while not self._stop.is_set():
            try:
                data = self._q.get(timeout=0.3)
            except queue.Empty:
                continue
            if self._rec.AcceptWaveform(data):
                res = json.loads(self._rec.Result())
                words = [w.get("word", "") for w in res.get("result", [])]
                if words:
                    self.on_words(words)
                elif res.get("text"):
                    self.on_words(str(res["text"]).split())
            else:
                part = json.loads(self._rec.PartialResult())
                if part.get("partial"):
                    self.on_partial(str(part["partial"]))

    def stop(self):
        self._stop.set()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        if self.thread:
            self.thread.join(timeout=2)


def list_input_devices():
    try:
        import sounddevice as sd
        return [f"{i}: {d['name']}" for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    except Exception as e:  # noqa: BLE001
        return [f"(нет доступа к звуковым устройствам: {e})"]


def has_vosk_model():
    return config.VOSK_MODEL_DIR.exists() and any(config.VOSK_MODEL_DIR.rglob("*"))

# -*- coding: utf-8 -*-
"""Лёгкий тест по прочитанным страницам.

Вопросы нормализуются к виду ``{"question", "options"[4], "answer", "about"}``
независимо от того, пришли они строкой из SQLite (колонки opt1..opt4)
или словарём из JSON (ключ "options"). Раньше экран теста обращался
к ``q["options"]`` у строк базы данных и падал с
``IndexError: No item with that key`` — тест было невозможно пройти.
"""
import random

from . import config


def normalize_question(q):
    """Привести вопрос к каноническому словарю. Битые отбрасываются (None).

    Понимает и строки SQLite (колонки opt1..opt4, question), и словари
    из JSON (ключ options, текст в question или q).
    """
    try:
        try:
            keys = set(q.keys()) if hasattr(q, "keys") else set()
        except Exception:  # noqa: BLE001
            return None
        if not keys:
            return None

        def _get(key, default=None):
            try:
                return q[key]
            except (KeyError, IndexError, TypeError):
                return default

        if "options" in keys:
            options = list(_get("options") or [])
        elif "opt1" in keys:  # строка таблицы questions
            options = [_get("opt1"), _get("opt2"), _get("opt3"), _get("opt4")]
        else:
            return None
        question = _get("question", _get("q", ""))
        answer = _get("answer", 0)
        about = _get("about", "")
        options = [str(o) for o in options]
        if len(options) != 4 or not str(question).strip():
            return None
        answer = int(answer)
        if not 0 <= answer <= 3:
            return None
        return {"question": str(question), "options": options,
                "answer": answer, "about": str(about or "")}
    except Exception:  # noqa: BLE001 — битый вопрос просто пропускаем
        return None


class QuizSession:
    """Показывает QUIZ_SIZE случайных вопросов, считает верные ответы."""

    def __init__(self, questions, size=None):
        size = size or config.QUIZ_SIZE
        try:
            size = max(1, int(size))
        except (TypeError, ValueError):
            size = config.QUIZ_SIZE
        pool = []
        for q in (questions or []):
            norm = normalize_question(q)
            if norm is not None:
                pool.append(norm)
        random.shuffle(pool)
        self.questions = pool[:size]
        self.index = 0
        self.correct = 0
        self.log = []  # (вопрос, ответ пользователя, верно?)

    def current(self):
        if self.index >= len(self.questions):
            return None
        return self.questions[self.index]

    def answer(self, choice: int) -> bool:
        q = self.current()
        if q is None:
            return False
        try:
            ok = int(choice) == int(q["answer"])
        except (TypeError, ValueError):
            ok = False
        if ok:
            self.correct += 1
        self.log.append((q["question"], choice, ok))
        self.index += 1
        return ok

    def finished(self):
        return self.index >= len(self.questions)

    def passed(self) -> bool:
        if not self.questions:
            return False
        return self.correct >= self.need_correct()

    def need_correct(self) -> int:
        if not self.questions:
            return 1
        return max(1, int(len(self.questions) * config.PASS_RATIO + 0.999))

    def result_text(self) -> str:
        verdict = "ЗАЧЁТ" if self.passed() else "НЕ ЗАЧЁТ"
        return (
            f"{verdict}: {self.correct} из {len(self.questions)} "
            f"(нужно {self.need_correct()} верных)."
        )

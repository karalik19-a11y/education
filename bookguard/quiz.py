# -*- coding: utf-8 -*-
"""Лёгкий тест по прочитанным страницам."""
import random

from . import config


class QuizSession:
    """Показывает QUIZ_SIZE случайных вопросов, считает верные ответы."""

    def __init__(self, questions, size=None):
        size = size or config.QUIZ_SIZE
        pool = list(questions)
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
        ok = int(choice) == int(q["answer"])
        if ok:
            self.correct += 1
        self.log.append((q["question"], choice, ok))
        self.index += 1
        return ok

    def finished(self):
        return self.index >= len(self.questions)

    def passed(self) -> bool:
        need = max(1, int(len(self.questions) * config.PASS_RATIO + 0.999))
        return self.correct >= need

    def need_correct(self) -> int:
        return max(1, int(len(self.questions) * config.PASS_RATIO + 0.999))

    def result_text(self) -> str:
        verdict = "ЗАЧЁТ" if self.passed() else "НЕ ЗАЧЁТ"
        return (
            f"{verdict}: {self.correct} из {len(self.questions)} "
            f"(нужно {self.need_correct()} верных)."
        )

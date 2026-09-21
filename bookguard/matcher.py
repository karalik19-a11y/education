# -*- coding: utf-8 -*-
"""Сопоставление распознанной речи с текстом книги.

Матчер двигает указатель по списку слов книги: каждое услышанное слово
ищется в ближайшем окне вперед (слово могло быть пропущено распознаванием).
Если слова долго «не попадают» в книгу — пользователь читает не ту книгу.
"""
from dataclasses import dataclass, field


@dataclass
class FeedResult:
    advanced_to: int = 0
    matched: int = 0
    skipped: int = 0
    not_matched: int = 0
    wrong_book: bool = False


@dataclass
class BookMatcher:
    words: list
    pos: int = 0
    window: int = 30
    fed: int = 0
    hits: int = 0
    misses: int = 0
    recent: list = field(default_factory=list)  # последние результаты (1/0)

    def _find(self, w: str):
        end = min(len(self.words), self.pos + self.window)
        # точное совпадение
        for i in range(self.pos, end):
            if self.words[i] == w:
                return i
        # совпадение по основе (Vosk часто ошибается в окончаниях)
        if len(w) >= 5:
            stem = w[:5]
            for i in range(self.pos, end):
                if len(self.words[i]) >= 5 and self.words[i][:5] == stem:
                    return i
        elif len(w) >= 3:
            stem = w[:3]
            for i in range(self.pos, end):
                if self.words[i][:3] == stem:
                    return i
        return None

    def feed(self, heard_words) -> FeedResult:
        res = FeedResult()
        for w in heard_words:
            w = (w or "").lower().replace("ё", "е").strip("«»\"'.,;:!?()-—…")
            if not w or not any(c.isalpha() for c in w):
                continue
            self.fed += 1
            i = self._find(w)
            if i is not None:
                res.skipped += (i - self.pos)
                self.pos = i + 1
                res.matched += 1
                self.hits += 1
                self.recent.append(1)
            else:
                res.not_matched += 1
                self.misses += 1
                self.recent.append(0)
            if len(self.recent) > 250:
                self.recent.pop(0)
        res.advanced_to = self.pos
        res.wrong_book = self.looks_like_wrong_book()
        return res

    def match_rate(self) -> float:
        if not self.recent:
            return 1.0
        return sum(self.recent) / len(self.recent)

    def looks_like_wrong_book(self) -> bool:
        """Тревога, если за последние 200 слов почти ничего не совпало."""
        if len(self.recent) < 120:
            return False
        return sum(self.recent[-200:]) / min(200, len(self.recent)) < 0.22

    def done(self) -> bool:
        return self.pos >= len(self.words)

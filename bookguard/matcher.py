# -*- coding: utf-8 -*-
"""Сопоставление распознанной речи с текстом книги.

Матчер двигает указатель по списку слов книги: каждое услышанное слово
ищется в ближайшем окне вперёд (слово могло быть пропущено распознаванием).
Если слова долго «не попадают» в книгу — пользователь читает не ту книгу.

Допуск к ошибкам распознавания:
  * точное совпадение;
  * совпадение по основе (Vosk часто ошибается в окончаниях);
  * «размытое» совпадение: расстояние Левенштейна 1–2 (опечатки/искажения).
"""
from dataclasses import dataclass, field


def levenshtein_limited(a: str, b: str, max_d: int) -> bool:
    """True, если расстояние Левенштейна(a, b) <= max_d (быстрая бanded-версия)."""
    if abs(len(a) - len(b)) > max_d:
        return False
    if a == b:
        return True
    # a — более короткое (или равное)
    if len(a) > len(b):
        a, b = b, a
    la, lb = len(a), len(b)
    prev = list(range(la + 1))
    for i in range(1, lb + 1):
        cur = [i] + [0] * la
        row_min = cur[0]
        bi = b[i - 1]
        for j in range(1, la + 1):
            cost = 0 if a[j - 1] == bi else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > max_d:
            return False
        prev = cur
    return prev[la] <= max_d


@dataclass
class FeedResult:
    advanced_to: int = 0
    matched: int = 0
    skipped: int = 0
    not_matched: int = 0
    wrong_book: bool = False
    per_word: list = field(default_factory=list)  # True/False по каждому поданному слову


@dataclass
class BookMatcher:
    words: list
    pos: int = 0
    window: int = 40
    fed: int = 0
    hits: int = 0
    misses: int = 0
    recent: list = field(default_factory=list)  # последние результаты (1/0)

    def _find(self, w: str):
        # коротким словам («и», «на») — короткое окно: иначе союз, услышанный
        # в чужом тексте, «перепрыгивает» на 30+ слов вперёд и даёт ложный прогресс
        eff_window = min(self.window, max(8, 3 * len(w) + 2))
        end = min(len(self.words), self.pos + eff_window)
        # 1) точное совпадение
        for i in range(self.pos, end):
            if self.words[i] == w:
                return i
        # 2) совпадение по основе (Vosk часто ошибается в окончаниях)
        if len(w) >= 5:
            stem = w[:5]
            for i in range(self.pos, end):
                if len(self.words[i]) >= 5 and self.words[i][:5] == stem:
                    return i
        elif len(w) >= 3:
            stem = w[:3]
            for i in range(self.pos, end):
                if len(self.words[i]) >= 3 and self.words[i][:3] == stem:
                    return i
        # 3) размытое совпадение (искажённое распознаванием слово)
        max_d = 2 if len(w) >= 9 else 1
        if max_d and 5 <= len(w) <= 24:
            best, best_d = None, max_d + 1
            for i in range(self.pos, end):
                cw = self.words[i]
                if abs(len(cw) - len(w)) > max_d or not cw:
                    continue
                if levenshtein_limited(w, cw, max_d):
                    d = sum(1 for x, y in zip(w, cw) if x != y) + abs(len(w) - len(cw))
                    if d < best_d:
                        best, best_d = i, d
                        if d == 0:
                            break
            if best is not None:
                return best
        return None

    def feed(self, heard_words) -> FeedResult:
        res = FeedResult()
        for w in heard_words:
            w = (w or "").lower().replace("ё", "е").strip("«»\"'.,;:!?()-—…")
            if not w or not any(c.isalpha() for c in w):
                res.per_word.append(None)  # не слово — для подсветки «нейтрально»
                continue
            self.fed += 1
            i = self._find(w)
            if i is not None:
                res.skipped += (i - self.pos)
                self.pos = i + 1
                res.matched += 1
                self.hits += 1
                res.per_word.append(True)
                self.recent.append(1)
            else:
                res.not_matched += 1
                self.misses += 1
                res.per_word.append(False)
                self.recent.append(0)
            if len(self.recent) > 300:
                self.recent.pop(0)
        res.advanced_to = self.pos
        res.wrong_book = self.looks_like_wrong_book()
        return res

    def match_rate(self) -> float:
        if not self.recent:
            return 1.0
        return sum(self.recent) / len(self.recent)

    def looks_like_wrong_book(self) -> bool:
        """Тревога, если за последние 250 слов почти ничего не совпало."""
        if len(self.recent) < 150:
            return False
        tail = self.recent[-250:]
        return sum(tail) / len(tail) < 0.20

    def done(self) -> bool:
        return self.pos >= len(self.words)

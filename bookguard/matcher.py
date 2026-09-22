# -*- coding: utf-8 -*-
"""Сопоставление распознанной речи с текстом книги.

Матчер двигает указатель по списку слов книги: каждое услышанное слово
ищется в ближайшем окне вперёд (слово могло быть пропущено распознаванием).
Если слова долго «не попадают» в книгу — пользователь читает не ту книгу.

Терпим к огрехам Vosk small-модели: перепутанные окончания, лёгкие
искажения звучания и слова-паразиты («эээ», «ммм»).
"""
from dataclasses import dataclass, field

# Слова-паразиты и междометия: молча пропускаем, в статистику не идут.
FILLERS = frozenset({
    "ээ", "эээ", "ээээ", "мм", "ммм", "эм", "эмм", "хм", "м",
    "м-м", "м-м-м", "э-э", "а-а", "аа", "ааа", "ну", "вот",
    "так", "это", "как", "бы", "значит", "типа",
})

# Частые ослышки маленькой модели -> то, что имелось в виду.
# Применяется только к услышанным словам.
PHONETIC_FIX = {
    "што": "что",
    "щто": "что",
    "че": "что",
    "чё": "что",
    "чево": "чего",
    "када": "когда",
    "кагда": "когда",
    "тада": "тогда",
    "щас": "сейчас",
    "щаз": "сейчас",
    "сечас": "сейчас",
    "ево": "его",
    "нево": "него",
    "сево": "сего",
    "савсем": "совсем",
    "зделать": "сделать",
    "хочеть": "хочет",
    "будто": "будто",
    "нибудь": "нибудь",
    "вобще": "вообще",
    "вообщем": "вообще",
    "канечно": "конечно",
    "канэчно": "конечно",
    "пожалуста": "пожалуйста",
    "здрасте": "здравствуйте",
    "спасибо": "спасибо",
}

_STRIP = "«»\"'.,;:!?()-—–…[]{}*№"


def clean_heard(w: str) -> str:
    """Нормализовать услышанное слово так же, как слова книги."""
    w = (w or "").lower().replace("ё", "е").strip(_STRIP).strip()
    if w in PHONETIC_FIX:
        w = PHONETIC_FIX[w]
    return w


def _lev1(a: str, b: str) -> bool:
    """True, если расстояние Левенштейна между словами <= 1 (быстрая проверка)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = 0
        for ca, cb in zip(a, b):
            if ca != cb:
                diff += 1
                if diff > 1:
                    return False
        return True
    # вставка/удаление одного символа
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    i = j = 0
    skipped = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


@dataclass
class FeedResult:
    advanced_from: int = 0
    advanced_to: int = 0
    matched: int = 0
    skipped: int = 0
    not_matched: int = 0
    wrong_book: bool = False


@dataclass
class BookMatcher:
    words: list
    pos: int = 0
    window: int = 40
    fed: int = 0
    hits: int = 0
    misses: int = 0
    recent: list = field(default_factory=list)  # последние результаты (1/0)

    def _window_for(self, w: str) -> int:
        """Окно поиска зависит от длины слова.

        Короткие слова («и», «в», «не») встречаются постоянно — искать их
        можно только вплотную, иначе один союз перепрыгнет через полстраницы
        (и чужой текст начнёт «засчитываться»). Длинные слова самобытны,
        для них окно широкое: переживём пропуски распознавания.
        """
        n = len(w)
        if n <= 2:
            return 8
        if n <= 4:
            return 16
        return self.window

    def _find(self, w: str):
        end = min(len(self.words), self.pos + self._window_for(w))
        # 1) точное совпадение
        for i in range(self.pos, end):
            if self.words[i] == w:
                return i
        # 2) совпадение по основе (Vosk часто ошибается в окончаниях)
        lw = len(w)
        if lw >= 5:
            stem = w[:5]
            for i in range(self.pos, end):
                if len(self.words[i]) >= 5 and self.words[i][:5] == stem:
                    return i
        if lw >= 4:
            stem = w[:4]
            for i in range(self.pos, end):
                if self.words[i][:4] == stem:
                    return i
        elif lw >= 3:
            stem = w[:3]
            for i in range(self.pos, end):
                if self.words[i][:3] == stem:
                    return i
        # 3) лёгкое искажение звучания (одна ошибка в слове 6+ букв)
        if lw >= 6:
            for i in range(self.pos, end):
                cand = self.words[i]
                if len(cand) >= 6 and cand[0] == w[0] and _lev1(cand, w):
                    return i
        return None

    def feed(self, heard_words) -> FeedResult:
        res = FeedResult(advanced_from=self.pos, advanced_to=self.pos)
        for raw in heard_words:
            w = clean_heard(raw)
            if not w or not any(c.isalpha() for c in w):
                continue
            if w in FILLERS:
                continue  # паразиты не двигают прогресс и не портят статистику
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
        """Тревога, если за последние ~200 слов почти ничего не совпало."""
        if len(self.recent) < 150:
            return False
        tail = self.recent[-200:]
        return sum(tail) / len(tail) < 0.20

    def done(self) -> bool:
        return self.pos >= len(self.words)

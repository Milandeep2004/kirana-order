"""AI order capture: turn a pasted Hinglish/English order message into draft order lines.

Design (see TDD): deterministic first, LLM only as an optional tiebreaker.
  1. split the message into candidate lines
  2. pull out quantity + unit; unclear units are ASKED, never guessed
  3. match the item words against (a) this retailer's aliases, then (b) the catalogue by fuzzy + keyword + size
  4. rank, compute a heuristic confidence, attach the top 2 alternatives
  5. optionally let an LLM choose among the top candidates when we are not sure (it can never invent a SKU)

The "confidence" is a heuristic (match strength + margin over the runner-up), not a calibrated probability.
Nothing here writes to the database; callers store the result as a draft that a person must confirm.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Callable

from rapidfuzz import fuzz

CASE_UNITS = {"peti", "petti", "petee", "case", "cases", "carton", "cartons", "box", "boxes", "cs"}
PIECE_UNITS = {"pcs", "pc", "piece", "pieces", "pkt", "pkts", "packet", "packets", "dozen", "darjan", "nag"}
SIZE_UNITS = {"kg", "kgs", "kilo", "kilos", "kilogram", "g", "gm", "gms", "gram", "grams", "ml", "l", "ltr", "litre", "liter"}
HINDI_NUMBERS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "che": 6,
    "saat": 7, "aath": 8, "nau": 9, "das": 10, "dus": 10, "bis": 20, "pachees": 25, "tees": 30, "pachas": 50, "gyarah": 11, "barah": 12, "pandrah": 15, "bees": 20,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
STOPWORDS = {
    "bhaiya", "bhai", "bhaiyya", "sir", "ji", "please", "pls", "plz", "chahiye", "chahie", "chaiye", "bhej",
    "bhejo", "bhejna", "dena", "de", "do", "dijiye", "wala", "wale", "wali", "ka", "ki", "ke", "hello", "hi",
    "namaste", "order", "aur", "and", "the", "of", "me", "mujhe", "hame", "hume",
}
SPLIT_RE = re.compile(r"[\n,;|+&]| aur | and ", re.IGNORECASE)
LIST_MARKER_RE = re.compile(r"^\s*(?:\d+[\).]|[-*•])\s+")
_NUM = r"\d+(?:\.\d+)?"
_UNIT_ALT = "|".join(sorted(CASE_UNITS | PIECE_UNITS, key=len, reverse=True))
_WORD_NUM = "|".join(sorted(HINDI_NUMBERS, key=len, reverse=True))
QTY_UNIT_RE = re.compile(rf"\b({_NUM}|{_WORD_NUM})\s*({_UNIT_ALT})\b")
SIZE_RE = re.compile(rf"\b({_NUM})\s*(kg|kgs|kilo|kilos|kilogram|g|gm|gms|gram|grams|ml|l|ltr|litre|liter)\b")
BARE_NUM_RE = re.compile(rf"\b({_NUM})\b")


@dataclass(frozen=True)
class CatalogItem:
    code: str
    name: str
    category: str
    keywords: str
    stock_cases: int


@dataclass
class Candidate:
    code: str
    name: str
    score: float
    stock_cases: int = 0


@dataclass
class CapturedLine:
    original_text: str
    description: str
    qty: int | None
    unit: str | None  # "case" | "piece" | None
    unit_question: bool
    sku_code: str | None
    confidence: str  # high | medium | low
    score: float
    source: str  # alias | fuzzy | llm | none
    alternatives: list[Candidate] = field(default_factory=list)
    note: str = ""


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^\w\s.]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _canon_units(t: str) -> str:
    t = re.sub(r"\b(kilogram|kilos?|kgs)\b", "kg", t)
    t = re.sub(r"\b(gms?|grams?)\b", "g", t)
    t = re.sub(r"\b(ltr|litre|liter)\b", "l", t)
    return t


def clean_phrase(text: str) -> str:
    """Canonical form of an item description, used for alias storage and matching."""
    t = _canon_units(normalize(text))
    return " ".join(w for w in t.split() if w not in STOPWORDS)


def sizes_in(text: str) -> set[float]:
    """Pack sizes mentioned, normalised to grams/ml. '1 kilo' -> 1000, '250 wala' -> 250."""
    t = _canon_units(normalize(text))
    out: set[float] = set()
    for num, unit in SIZE_RE.findall(t):
        v = float(num)
        out.add(v * 1000 if unit == "kg" else v)
    for m in re.finditer(rf"\b({_NUM})\s+(?:wala|wale|wali)\b", t):
        out.add(float(m.group(1)))
    return out


def strip_quantity(text: str) -> str:
    """Item words only: remove '5 peti' style quantity and filler words."""
    t = _canon_units(normalize(LIST_MARKER_RE.sub("", text)))
    t = QTY_UNIT_RE.sub(" ", t)
    return clean_phrase(t)


def split_message(text: str) -> list[str]:
    parts = []
    for chunk in SPLIT_RE.split(text):
        chunk = LIST_MARKER_RE.sub("", chunk).strip()
        if chunk:
            parts.append(chunk)
    return parts


def extract_quantity(segment: str) -> tuple[int | None, str | None, bool, str]:
    """Return (qty, unit, unit_question, note). Never guesses an unclear unit."""
    t = _canon_units(normalize(segment))
    m = QTY_UNIT_RE.search(t)
    if m:
        raw, unit = m.group(1), m.group(2)
        qty = HINDI_NUMBERS.get(raw)
        if qty is None:
            f = float(raw)
            qty = int(f) if f == int(f) else None
        if qty is None:
            return None, None, True, "Quantity looks fractional. Please enter whole cases."
        if unit in CASE_UNITS:
            return qty, "case", False, ""
        return qty, "piece", True, "You wrote pieces/packets. We sell by the case. Please confirm the number of cases."
    # no explicit unit: look for a bare number that is not a pack size
    stripped = SIZE_RE.sub(" ", t)
    stripped = re.sub(rf"\b{_NUM}\s+(?:wala|wale|wali)\b", " ", stripped)
    nums = BARE_NUM_RE.findall(stripped)
    if len(nums) == 1 and float(nums[0]) == int(float(nums[0])):
        return int(float(nums[0])), None, True, "No unit given. Is this cases or pieces?"
    if len(nums) > 1:
        return None, None, True, "More than one number found. Please enter the quantity."
    return None, None, True, "No quantity found. Please enter cases."


def _bare_sizes(segment: str, known: set[float]) -> set[float]:
    """A number written without a unit ('detergent 500', 'tea 100') counts as a pack size when it equals
    a size we actually sell. The quantity ('3 peti') and unit-ed sizes ('1 kilo') are removed first."""
    t = _canon_units(normalize(segment))
    t = QTY_UNIT_RE.sub(" ", t)
    t = SIZE_RE.sub(" ", t)
    return {float(n) for n in BARE_NUM_RE.findall(t) if float(n) in known}


def _item_words(text: str) -> list[str]:
    """Words that describe the item (no numbers, no unit/filler words)."""
    out = []
    for w in clean_phrase(text).split():
        if re.fullmatch(_NUM, w) or w in CASE_UNITS or w in PIECE_UNITS or w in SIZE_UNITS:
            continue
        out.append(w)
    return out


def _sku_words(item: CatalogItem) -> set[str]:
    words = set()
    for src in (item.category, item.name, item.keywords.replace(",", " ")):
        for w in normalize(src).split():
            if not re.fullmatch(_NUM, w) and w not in SIZE_UNITS and w not in {"case", "of"}:
                words.add(w)
    return words


def _sku_sizes(item: CatalogItem) -> set[float]:
    return sizes_in(item.name) | {float(n) for n in re.findall(r"\b(\d+)\b", item.keywords.replace(",", " ")) if float(n) >= 50}


def score_item(desc: str, item: CatalogItem, want: set[float] | None = None) -> float:
    words = _item_words(desc)
    if not words:
        return 0.0
    sku_words = _sku_words(item)
    sku_text = " ".join(sorted(sku_words))
    s = fuzz.token_set_ratio(" ".join(words), sku_text) / 100
    hits = sum(1 for w in words if any(fuzz.ratio(w, sw) >= 85 for sw in sku_words))
    kw = hits / len(words)
    score = 0.8 * s + 0.2 * kw
    want = sizes_in(desc) if want is None else want
    if want:
        score += 0.15 if want & _sku_sizes(item) else -0.25
    # not clipped at 1.0: a size match must be able to separate two items that both match the words perfectly
    return max(0.0, score)


def _confidence(top: float, runner_up: float) -> tuple[str, str]:
    margin = top - runner_up
    if top < 0.45:
        return "low", "No good match. Please pick the item."
    if margin < 0.05:
        return "low", "Several items match equally well. Please choose."
    if top >= 0.85 and margin >= 0.10:
        return "high", ""
    return "medium", ""


_RANK = {"low": 0, "medium": 1, "high": 2}
HISTORY_BONUS_PER_ORDER = 0.04
HISTORY_BONUS_MAX = 0.12


def _history_bonus(c: Candidate, history: dict[str, int]) -> float:
    """Small nudge for items this shop has ordered before; ignored when the text barely matches."""
    n = history.get(c.code, 0)
    return min(HISTORY_BONUS_MAX, HISTORY_BONUS_PER_ORDER * n) if n and c.score >= 0.3 else 0.0


LLMPicker = Callable[[str, list[Candidate]], str | None]


def make_anthropic_picker(api_key: str, model: str, post=None, timeout: float = 6.0) -> LLMPicker:
    """Optional tiebreaker. Sends only the item words and candidate names (no retailer data)."""
    def pick(description: str, cands: list[Candidate]) -> str | None:
        import httpx

        poster = post or httpx.post
        prompt = (
            "A kirana shop owner wrote this item in an order message (Hinglish possible): "
            f'"{description}".\nWhich catalogue item did they most likely mean? Candidates:\n'
            + "\n".join(f"- {c.code}: {c.name}" for c in cands)
            + '\nReply with JSON only: {"code": "<one candidate code or null if unsure>"}'
        )
        try:
            r = poster(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": 60, "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout,
            )
            r.raise_for_status()
            text = r.json()["content"][0]["text"]
            code = json.loads(re.search(r"\{.*\}", text, re.S).group(0)).get("code")
        except Exception:
            return None
        return code if code in {c.code for c in cands} else None

    return pick


def capture(
    message: str,
    catalog: list[CatalogItem],
    aliases: dict[str, str] | None = None,
    llm: LLMPicker | None = None,
    history: dict[str, int] | None = None,
) -> list[CapturedLine]:
    """history: SKU code -> number of this shop's earlier orders containing it.

    It only nudges ranking (see HISTORY_BONUS) so a shop's usual item wins a tie. It can never
    make a line "high" confidence on its own: a person still checks it.
    """
    history = history or {}
    aliases = {clean_phrase(k): v for k, v in (aliases or {}).items()}
    by_code = {c.code: c for c in catalog}
    known_sizes = set().union(*(_sku_sizes(c) for c in catalog)) if catalog else set()
    results: list[CapturedLine] = []
    for seg in split_message(message):
        desc = strip_quantity(seg)
        if not _item_words(desc) and not QTY_UNIT_RE.search(_canon_units(normalize(seg))):
            continue  # greeting / filler, nothing orderable
        qty, unit, unit_q, qnote = extract_quantity(seg)
        want = sizes_in(seg) | _bare_sizes(seg, known_sizes)
        base = sorted(
            (Candidate(c.code, c.name, round(score_item(desc, c, want), 3), c.stock_cases) for c in catalog),
            key=lambda c: c.score, reverse=True,
        )
        ranked = sorted(
            (Candidate(c.code, c.name, round(c.score + _history_bonus(c, history), 3), c.stock_cases) for c in base),
            key=lambda c: c.score, reverse=True,
        )
        base_top = base[0].code
        source = "fuzzy"
        # 1) retailer's own alias wins
        alias_code = None
        for phrase, code in aliases.items():
            if code in by_code and (phrase == desc or fuzz.ratio(phrase, desc) >= 92):
                alias_code = code
                break
        if alias_code:
            top = next(c for c in ranked if c.code == alias_code)
            top = Candidate(top.code, top.name, 0.99, top.stock_cases)
            others = [c for c in ranked if c.code != alias_code]
            ranked = [top] + others
            conf, cnote, source = "high", "Matched from this shop's earlier orders.", "alias"
        else:
            conf, cnote = _confidence(ranked[0].score, ranked[1].score if len(ranked) > 1 else 0.0)
            base_conf, _ = _confidence(base[0].score, base[1].score if len(base) > 1 else 0.0)
            used = history.get(ranked[0].code, 0)
            if used and (ranked[0].code != base_top or _RANK[conf] > _RANK[base_conf]):
                # history decided this line: suggest it, but never present it as certain
                if conf == "high":
                    conf = "medium"
                cnote = f"You have ordered this {used} time{'s' if used != 1 else ''} before. Please check."
            if conf != "high" and llm and ranked[0].score >= 0.3:
                picked = llm(desc, ranked[:3])
                if picked:
                    chosen = next(c for c in ranked if c.code == picked)
                    ranked = [chosen] + [c for c in ranked if c.code != picked]
                    conf, cnote, source = ("medium" if conf == "low" or chosen.score >= 0.45 else "low"), "", "llm"
                    if chosen.score < 0.45:
                        conf = "low"
        if conf == "low" and ranked[0].score < 0.45 and source != "llm":
            sku, source = None, "none"
        else:
            sku = ranked[0].code
        alts = [c for c in ranked if c.code != sku][:2]
        note = " ".join(n for n in (cnote, qnote) if n)
        results.append(CapturedLine(
            original_text=seg.strip(), description=desc, qty=qty, unit=unit, unit_question=unit_q,
            sku_code=sku, confidence=conf, score=min(1.0, ranked[0].score) if sku else 0.0, source=source,
            alternatives=alts, note=note,
        ))
    return results

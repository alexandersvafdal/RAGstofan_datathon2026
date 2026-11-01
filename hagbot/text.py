"""Text helpers: Icelandic-aware normalisation, language detection, number formatting."""

import math
import re
import unicodedata

_IS_CHARS = set("þðæöáéíóúýÞÐÆÖÁÉÍÓÚÝ")
_IS_WORDS = {"hvað", "hve", "hversu", "margir", "margar", "búa", "árið", "og", "í", "á", "er", "var", "hvert", "hver",
             "hverjir", "hverjar", "eru", "undirflokkar"}
# English function words: "how many seyðisfjörður populations would fit in reykjavíks population" is English even
# with its place names typed in lower case
_EN_WORDS = {"how", "many", "much", "what", "which", "when", "where", "who", "why", "the", "of", "in", "is", "are",
             "was", "were", "would", "could", "did", "does", "do", "has", "have", "and", "per", "than", "from", "to",
             "with", "there", "people", "population", "number"}


def normalize(s: str) -> str:
    s = s.lower().replace("þ", "th").replace("æ", "ae").replace("ð", "d").replace("ö", "o")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(s))


def detect_language(s: str) -> str:
    words = re.findall(r"\w+", s)
    low = {w.lower() for w in words}
    if len(low & _EN_WORDS) >= 2 and len(low & _IS_WORDS) < 2:
        return "en"
    # Place names (Reykjavík, Kópavogur) carry Icelandic letters in English questions, so skip capitalised words
    if any(c in _IS_CHARS for w in words[1:] if not w[0].isupper() for c in w):
        return "is"
    if words and words[0].lower() in _IS_WORDS:
        return "is"
    return "is" if len({w.lower() for w in words} & _IS_WORDS) >= 2 else "en"


def fmt_number(x: float, lang: str, decimals: int | None = None) -> str:
    if decimals is None:
        decimals = 0 if float(x).is_integer() else 1
        if x and abs(x) < 1:   # 0.0041 is not "0.0": two significant digits for a small number, no trailing zero
            g = f"{abs(x):.2g}"
            decimals = len(g.split(".")[1]) if "." in g and "e" not in g else 6
    s = f"{x:,.{decimals}f}"
    if lang == "is":  # 139.804,5
        s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return s


def fmt_value(x: float, unit: str, lang: str) -> str:
    if unit == "%":
        return fmt_number(x, lang, None if 0 < abs(x) < 0.1 else 1) + "%"
    return fmt_number(x, lang)


# A unit's multiplier word: "thousand tonnes", "Kilotonnes", "Million ISK", "millj. kr.", "ISK billion"
_SCALE = [(re.compile(r"\b(thousands?|[þt]h?[úu]s(?:\.|und(?:ir|um)?)?)\s*", re.I), 1e3),
          (re.compile(r"\b(kilo|kíló)(?=[a-záðéíóúýþæö])", re.I), 1e3),
          (re.compile(r"\b(millions?|millj\.?|millj[óo]n(?:ir|um|a)?)\s*", re.I), 1e6),
          (re.compile(r"\b(billions?|milljar[ðd](?:ar|ur|a)?|ma\.)\s*", re.I), 1e9)]


def unscale(value: float, unit: str) -> tuple[float, str]:
    """A value below 1 in a scaled unit, in the plain unit instead: 0.0041 kilotonnes is 4.1 tonnes, 0.02 million
    ISK is 20,000 ISK. The multiplier word leaves the unit and enters the number, as often as needed."""
    while unit and 0 < abs(value) < 1:
        for pattern, factor in _SCALE:
            new = pattern.sub("", unit, count=1).strip()
            if new != unit:
                value, unit = value * factor, new
                break
        else:
            break
    return value, unit


def numbers_in(text: str) -> list[float]:
    """Numbers as written in a sentence, either locale. Used to check the composer invented nothing."""
    out = []
    for m in re.findall(r"[-−]?\d[\d.,]*", text):
        m = m.replace("−", "-").rstrip(".,")
        for cand in (m.replace(",", ""), m.replace(".", "").replace(",", ".")):
            try:
                out.append(float(cand))
            except ValueError:
                pass
    return out


# Index bases. Index tables offer several bases ("Base 2021", "Base 1983", "Grunnur 1988"); the current one is what
# Statistics Iceland publishes as the index today, so it is the default. An older base only when the question names
# it: "base 1983", "grunnur 1983", "grunnári 1983", "1983=100".
_BASE_LABEL = re.compile(r"\b(?:base|grunn\w*)\s*(?:year\s*|ár\w*\s*)?((?:18|19|20)\d\d)\b", re.I)
_BASE_ASKED = re.compile(r"\b(?:base|grunn\w*)\W{0,3}(?:year\W{0,3}|ar\w*\W{0,3})?((?:18|19|20)\d\d)\b"
                         r"|\b((?:18|19|20)\d\d)\W{0,3}(?:base|grunn\w*)\b|\b((?:18|19|20)\d\d)\s*=\s*100\b")


def base_year(label: str) -> int | None:
    """1983 for "Base 1983" or "Grunnur 1983"; None for a label that is not an index base."""
    m = _BASE_LABEL.search(label)
    return int(m.group(1)) if m else None


def asked_bases(question: str) -> set[int]:
    """The index base years a question names explicitly."""
    return {int(next(g for g in m.groups() if g)) for m in _BASE_ASKED.finditer(normalize(question))}


_MONTHS = {"en": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(),
           "is": "jan. feb. mar. apr. maí jún. júl. ágú. sep. okt. nóv. des.".split()}


def fmt_period(period: str, lang: str) -> str:
    """2016M01 -> "Jan 2016" / "jan. 2016", 2016Q2 -> "Q2 2016" / "2. ársfj. 2016"; anything else as it is."""
    m = re.fullmatch(r"(\d{4})([MQ])(\d{1,2})", period)
    if not m:
        return period
    year, kind, n = m.group(1), m.group(2), int(m.group(3))
    if kind == "M":
        return f"{_MONTHS['is' if lang == 'is' else 'en'][n - 1]} {year}"
    return f"{n}. ársfj. {year}" if lang == "is" else f"Q{n} {year}"

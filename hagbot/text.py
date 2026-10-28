"""Text helpers: Icelandic-aware normalisation, language detection, number formatting."""

import re
import unicodedata

_IS_CHARS = set("þðæöáéíóúýÞÐÆÖÁÉÍÓÚÝ")
_IS_WORDS = {"hvað", "hve", "hversu", "margir", "margar", "búa", "árið", "og", "í", "á", "er", "var", "hvert", "hver"}


def normalize(s: str) -> str:
    s = s.lower().replace("þ", "th").replace("æ", "ae").replace("ð", "d").replace("ö", "o")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(s))


def detect_language(s: str) -> str:
    words = re.findall(r"\w+", s)
    # Place names (Reykjavík, Kópavogur) carry Icelandic letters in English questions, so skip capitalised words
    if any(c in _IS_CHARS for w in words[1:] if not w[0].isupper() for c in w):
        return "is"
    if words and words[0].lower() in _IS_WORDS:
        return "is"
    return "is" if len({w.lower() for w in words} & _IS_WORDS) >= 2 else "en"


def fmt_number(x: float, lang: str, decimals: int | None = None) -> str:
    if decimals is None:
        decimals = 0 if float(x).is_integer() else 1
    s = f"{x:,.{decimals}f}"
    if lang == "is":  # 139.804,5
        s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return s


def fmt_value(x: float, unit: str, lang: str) -> str:
    if unit == "%":
        return fmt_number(x, lang, 1) + "%"
    return fmt_number(x, lang)


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

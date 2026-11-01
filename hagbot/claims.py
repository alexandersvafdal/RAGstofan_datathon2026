"""Render source-bound claims: number, population, period and unit stay together."""

import re
from collections import defaultdict
from math import isfinite

from .outcomes import AnswerFailure
from .schemas import AnswerClaim, AnswerPart, Fact
from .text import normalize

PERIOD = re.compile(r"\b(?:18|19|20)\d\d(?:M\d\d|Q[1-4])?\b")


def _value(fact: Fact) -> str:
    return fact.formatted + (f" {fact.unit}" if fact.unit not in ("", "%") else "")


def _periods(part, indices):
    periods = {p for i in indices for p in (part.facts[i].periods or PERIOD.findall(part.facts[i].label))}
    return sorted(periods or set(part.context.periods if part.context else []))


def _key(fact):
    return PERIOD.sub("", fact.label).strip(" ,"), fact.unit


def _series(part, indices):
    points = []
    for i in indices:
        fact = part.facts[i]
        periods = fact.periods or PERIOD.findall(fact.label)
        if len(periods) == 1:
            points.append((periods[0], i))
    points.sort()
    if len(points) <= 3 or len({p for p, _ in points}) != len(points):
        return None
    first, last = points[0][1], points[-1][1]
    peak = max(points, key=lambda point: part.facts[point[1]].value)[1]
    return list(dict.fromkeys([first, last, peak])), {i for _, i in points}


def _text(part, indices, kind, lang, derived_count=0):
    if kind == "series":
        first, last = indices[:2]
        peak = max(indices, key=lambda i: part.facts[i].value)
        p0, p1, pk = (_periods(part, [i])[0] for i in (first, last, peak))
        return (f"{_key(part.facts[first])[0]}: {_value(part.facts[first])} ({p0}) → "
                f"{_value(part.facts[last])} ({p1}); {'hæst' if lang == 'is' else 'highest'} "
                f"{_value(part.facts[peak])} ({pk})")
    fact = part.facts[indices[-1]]
    periods = _periods(part, indices)
    dated = f" ({', '.join(periods)})" if periods and not PERIOD.search(fact.label) else ""
    expression = (f" [{part.computation.expression}]" if kind == "calculation" and part.computation
                  and derived_count == 1 else "")
    return f"{fact.label}{dated}: {_value(fact)}{expression}"


def _make(part, number, indices, kind, ordinal, lang, derived_count=0):
    facts = [part.facts[i] for i in indices]
    if any(not isfinite(f.value) for f in facts):
        raise AnswerFailure("unverified", "invalid_claim_value")
    return AnswerClaim(id=f"p{number}c{ordinal}", part=number, fact_indices=indices, kind=kind,
                       labels=[f.label for f in facts], values=[f.value for f in facts], units=[f.unit for f in facts],
                       periods=_periods(part, indices), text=_text(part, indices, kind, lang, derived_count),
                       source_ids=list(dict.fromkeys(t for f in facts for t in (f.source_ids or [part.table.id]))))


def build_claims(parts: list[AnswerPart], lang: str) -> list[AnswerClaim]:
    claims = []
    for number, part in enumerate(parts):
        if not part.facts:
            raise AnswerFailure("unverified", "incomplete_claims")
        if part.computation:
            derived = [i for i, f in enumerate(part.facts) if f.basis_indices]
            if not derived:
                raise AnswerFailure("unverified", "missing_calculation_basis")
            for ordinal, i in enumerate(derived):
                basis = part.facts[i].basis_indices
                if any(index < 0 or index >= i for index in basis):
                    raise AnswerFailure("unverified", "invalid_calculation_basis")
                claims.append(_make(part, number, list(dict.fromkeys([*basis, i])), "calculation", ordinal, lang, len(derived)))
        else:
            groups = defaultdict(list)
            for i, fact in enumerate(part.facts):
                groups[_key(fact)].append(i)
            ordinal = 0
            for indices in groups.values():
                summary = _series(part, indices) if len(indices) > 3 else None
                if summary and summary[1] == set(indices):
                    claims.append(_make(part, number, summary[0], "series", ordinal, lang))
                    ordinal += 1
                else:
                    for i in indices:
                        claims.append(_make(part, number, [i], "value", ordinal, lang))
                        ordinal += 1
    validate_claims(claims, parts)
    return claims


def validate_claims(claims: list[AnswerClaim], parts: list[AnswerPart]) -> None:
    if {c.part for c in claims} != set(range(len(parts))) or len({c.id for c in claims}) != len(claims):
        raise AnswerFailure("unverified", "incomplete_claims")
    sources, covered, groups = {p.table.id for p in parts}, defaultdict(set), defaultdict(list)
    counts = {i: sum(bool(f.basis_indices) for f in p.facts) for i, p in enumerate(parts)}
    for i, part in enumerate(parts):
        for j, fact in enumerate(part.facts):
            groups[(i, _key(fact))].append(j)
    for c in claims:
        part = parts[c.part]
        if not c.fact_indices or len(set(c.fact_indices)) != len(c.fact_indices) or any(not 0 <= i < len(part.facts) for i in c.fact_indices):
            raise AnswerFailure("unverified", "invalid_claim_basis")
        if c.kind == "series" and len(c.fact_indices) < 2:
            raise AnswerFailure("unverified", "invalid_series_claim")
        facts = [part.facts[i] for i in c.fact_indices]
        expected_sources = list(dict.fromkeys(t for f in facts for t in (f.source_ids or [part.table.id])))
        if c.source_ids != expected_sources or not set(c.source_ids) <= sources:
            raise AnswerFailure("unverified", "ungrounded_claim_source")
        if (c.labels != [f.label for f in facts] or c.values != [f.value for f in facts] or c.units != [f.unit for f in facts]
                or c.periods != _periods(part, c.fact_indices) or c.text not in {_text(part, c.fact_indices, c.kind, lang, counts[c.part]) for lang in ("en", "is")}):
            raise AnswerFailure("unverified", "claim_binding_changed")
        if c.kind == "calculation":
            i = c.fact_indices[-1]
            if not part.computation or c.fact_indices != list(dict.fromkeys([*part.facts[i].basis_indices, i])) or not part.facts[i].basis_indices:
                raise AnswerFailure("unverified", "invalid_calculation_basis")
            covered[c.part].add(i)
        elif c.kind == "series":
            summary = _series(part, groups[(c.part, _key(part.facts[c.fact_indices[0]]))])
            if part.computation or summary is None or c.fact_indices != summary[0]:
                raise AnswerFailure("unverified", "invalid_series_claim")
            covered[c.part].update(summary[1])
        else:
            if part.computation or len(c.fact_indices) != 1:
                raise AnswerFailure("unverified", "invalid_value_claim")
            covered[c.part].update(c.fact_indices)
    for i, part in enumerate(parts):
        required = {j for j, f in enumerate(part.facts) if f.basis_indices} if part.computation else set(range(len(part.facts)))
        if covered[i] != required:
            raise AnswerFailure("unverified", "incomplete_claims")


def render_claims(claims: list[AnswerClaim], parts: list[AnswerPart], lang: str) -> str:
    validate_claims(claims, parts)
    source = "Heimild: Hagstofa Íslands" if lang == "is" else "Source: Statistics Iceland"
    out, refs = [], []
    for number, part in enumerate(parts):
        sentences = [c.text for c in claims if c.part == number]
        forecast = bool(re.search(r"forecast|projection|spa\b|mannfjoldaspa", normalize(part.table.title)))
        qualifier = ("Útgefin spá. " if lang == "is" else "Published forecast. ") if forecast else ""
        out.append(f"{part.table.title}. {qualifier}" + "; ".join(sentences) + ".")
        ref = part.table.id
        if part.table.source_kind == "snapshot" and part.table.snapshot_at:
            ref += f" ({'gagnasafn' if lang == 'is' else 'snapshot'} {part.table.snapshot_at})"
        if ref not in refs:
            refs.append(ref)
    return " ".join(out) + f" {source}, {', '.join(refs)}."

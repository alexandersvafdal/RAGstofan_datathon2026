"""Source-bound statistical contexts and explicit rules for cross-table arithmetic."""

import re

from .outcomes import AnswerFailure
from .schemas import AnswerPart, StatisticalContext
from .semantics import _tags, _unit
from .sources import TableMeta
from .specification import QuestionSpec
from .text import normalize, tokens

DEFINITIONS = ("statistic", "adjustment", "price_basis", "tax_basis")
TOTALS = {"total", "all", "alls", "samtals", "all ages", "both sexes", "whole country", "landid allt"}


def canonical(text: str) -> str:
    value = " ".join(tokens(text))
    aliases = {"people": "persons", "person": "persons", "inhabitants": "persons", "gdp": "gross domestic product",
               "men": "males", "karlar": "males", "women": "females", "konur": "females"}
    return "total" if value in TOTALS else aliases.get(value, value)


def context_for(meta: TableMeta, selected: dict[str, list[str]], spec: QuestionSpec) -> StatisticalContext:
    labels = [v.label_of(c) for v in meta.variables if not v.time for c in selected[v.code]]
    source_text = " ".join([meta.title, meta.description, *meta.notes,
                            *(note for v in meta.variables for note in v.notes),
                            *(note for v in meta.variables for code in selected[v.code] for note in v.value_notes.get(code, []))])
    definitions = {}
    for facet in DEFINITIONS:
        stated = [canonical(r.value) for r in spec.requirements if r.facet == facet]
        observed = _tags(facet, " ".join(labels)) or _tags(facet, source_text)
        if stated or observed:
            definitions[facet] = sorted(set(stated) | observed)
    population = {}
    for var in meta.variables:
        text = normalize(var.text)
        facet = next((name for name, pattern in {
            "sex": r"\bsex\b|\bgender\b|\bkyn\w*", "age": r"\bages?\b|\baldur\w*", "citizenship": r"citizen|rikisfang",
            "origin": r"origin|upprun", "birthplace": r"birth|faedingarland"}.items() if re.search(pattern, text)), None)
        if facet and not var.time:
            population[facet] = sorted({canonical(var.label_of(c)) for c in selected[var.code]})
    for req in spec.requirements:
        if req.facet == "population":
            population.setdefault("requested", []).append(canonical(req.value))
    measure = canonical(next(r.value for r in spec.requirements if r.facet == "measure"))
    unit = _unit(meta.unit) if meta.unit_source == "source" else None
    if unit is None and meta.unit_source == "source" and canonical(meta.unit) in {"number", "count", "fjoldi"}:
        unit = ("persons" if measure == "population" else f"count:{measure}", 1)
    if measure == "population" and unit and unit[0] == "persons" and "statistic" not in definitions:
        definitions["statistic"] = ["total"]
    periods = [c for v in meta.variables if v.time for c in selected[v.code]]
    text = normalize(source_text)
    reference = ("annual_average" if re.search(r"average population|annual average|medalmannfjold|\bmid[ -]?year\b", text) else
                 "january_1" if re.search(r"\b1\.?\s*(?:january|januar)\b|\bjanuary\s+1(?:st)?\b|beginning of (?:the )?year", text) else
                 "period_total" if measure in {"births", "deaths", "overnight stays", "exports", "imports", "gross domestic product"} else
                 "point_in_time" if re.search(r"end of|as of|at the end|lok (?:ars|manad)|i lok", text) else "unknown")
    return StatisticalContext(measure=measure, definitions=definitions,
                              geography=sorted({canonical(r.value) for r in spec.requirements if r.facet == "geography"}),
                              population=population, periods=periods, reference=reference,
                              unit=unit[0] if unit else "", scale=unit[1] if unit else 1,
                              explicit=sorted({r.facet for r in spec.requirements if r.origin != "default"}))


def _contexts(a: AnswerPart, b: AnswerPart):
    if a.context is None or b.context is None:
        raise AnswerFailure("unverified", "unknown_comparison_context")
    return a.context, b.context


def _equal_definitions(a: StatisticalContext, b: StatisticalContext) -> None:
    if a.measure != b.measure:
        raise AnswerFailure("unavailable", "incompatible_comparison_measure")
    if a.measure in {"income", "wealth", "wages"} and not a.definitions.get("statistic"):
        raise AnswerFailure("unverified", "unknown_comparison_statistic")
    for facet in DEFINITIONS:
        left, right = a.definitions.get(facet, []), b.definitions.get(facet, [])
        if left != right:
            status = "unavailable" if left and right else "unverified"
            raise AnswerFailure(status, "incompatible_comparison_definition", facet)
    if not a.unit or not b.unit:
        raise AnswerFailure("unverified", "unknown_comparison_unit")
    if (a.unit, a.scale) != (b.unit, b.scale):
        raise AnswerFailure("unavailable", "incompatible_comparison_unit")


def _population_subset(part: dict, whole: dict) -> bool:
    # An absent axis is unfiltered, like a source's explicit total. Different
    # non-total age/sex/origin groups are not assumed to contain one another.
    for facet in set(part) | set(whole):
        a, b = set(part.get(facet, ["total"])), set(whole.get(facet, ["total"]))
        if b != {"total"} and not a <= b:
            return False
    return True


def validate_combination(first: AnswerPart, second: AnswerPart, operation: str) -> None:
    a, b = _contexts(first, second)
    _equal_definitions(a, b)
    if not a.periods or not b.periods:
        raise AnswerFailure("unverified", "unknown_comparison_period")
    frequency = lambda periods: {"annual" if re.fullmatch(r"\d{4}", p) else "monthly" if re.fullmatch(r"\d{4}M\d{2}", p)
                                 else "quarterly" if re.fullmatch(r"\d{4}Q[1-4]", p) else "unknown" for p in periods}
    if frequency(a.periods) != frequency(b.periods) or "unknown" in frequency(a.periods):
        raise AnswerFailure("unavailable", "incompatible_comparison_frequency")
    if a.reference != b.reference or a.reference == "unknown":
        raise AnswerFailure("unverified", "incompatible_comparison_reference")
    if first.table.source_kind != second.table.source_kind or first.table.snapshot_at != second.table.snapshot_at:
        raise AnswerFailure("unverified", "incompatible_source_vintages")
    if operation == "share":
        if a.periods != b.periods:
            raise AnswerFailure("unavailable", "incompatible_comparison_period")
        # Cross-table containment needs explicit common geography; otherwise
        # use a table with both the requested place and its published total.
        if not a.geography or a.geography != b.geography:
            raise AnswerFailure("unverified", "unproven_geographic_containment")
        if not _population_subset(a.population, b.population):
            raise AnswerFailure("unavailable", "incompatible_share_population")
    else:
        if a.periods != b.periods and not ("period" in a.explicit and "period" in b.explicit):
            raise AnswerFailure("unverified", "unaligned_default_periods")
        if a.geography != b.geography and not ("geography" in a.explicit and "geography" in b.explicit):
            raise AnswerFailure("unverified", "unaligned_default_geography")
        if a.population != b.population and not ("population" in a.explicit and "population" in b.explicit):
            raise AnswerFailure("unavailable", "incompatible_comparison_population")


def validate_population_ratio(first: AnswerPart, second: AnswerPart, year: str | None, *, dated: bool = False) -> None:
    a, b = _contexts(first, second)
    if b.measure != "population" or b.unit != "persons":
        raise AnswerFailure("unavailable", "invalid_population_denominator")
    if not a.unit or a.unit in {"percent", "index"}:
        raise AnswerFailure("unverified", "unknown_ratio_unit")
    if not a.geography or a.geography != b.geography:
        raise AnswerFailure("unavailable", "incompatible_ratio_geography")
    if any(set(values) != {"total"} for values in b.population.values()):
        raise AnswerFailure("unavailable", "incompatible_ratio_population")
    if b.reference != "january_1":
        raise AnswerFailure("unverified", "incompatible_population_reference")
    explicit_dates = dated and "period" in a.explicit and "period" in b.explicit
    if (len(a.periods) != 1 or len(b.periods) != 1 or not re.fullmatch(r"\d{4}", a.periods[0])
            or (a.periods[0] != b.periods[0] and not explicit_dates) or (year and b.periods[0] != year)):
        raise AnswerFailure("unavailable", "incompatible_ratio_period")
    if first.table.source_kind != second.table.source_kind or first.table.snapshot_at != second.table.snapshot_at:
        raise AnswerFailure("unverified", "incompatible_source_vintages")

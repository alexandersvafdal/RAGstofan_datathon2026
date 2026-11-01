"""Per-inhabitant questions, no model: is the "per ..." clause read right, and is a published rate used when one fits?

    python -m eval.ratio_check

Each case gives the numerator question, scale, place, whether the population date is named, and the route:
a table ID (or IDs joined by |) when a published rate should be read, "divide" when the division must be done here.
"""

from hagbot.catalog import Catalog
from hagbot.config import settings
from hagbot.steps.ratio import detect_ratio, stored_rate

CASES = [
    # (question, numerator, scale, place, dated, route)
    ("What was GDP per capita in 2024?", "What was GDP in 2024?", 1, None, False, "THJ01401|THJ01000"),
    ("Hver var landsframleiðsla á mann árið 2024?", "Hver var landsframleiðsla árið 2024?", 1, None, False,
     "THJ01401|THJ01000"),
    ("How many litres of alcohol per inhabitant were consumed in Iceland in 2024?",
     "How many litres of alcohol were consumed in Iceland in 2024?", 1, None, False, "HEI07202"),
    ("How many vehicles per 1,000 inhabitants were registered in Iceland in 2024?",
     "How many vehicles were registered in Iceland in 2024?", 1000, None, False, "SAM03105"),
    ("What was public expenditure on health care per capita in 2025?",
     "What was public expenditure on health care in 2025?", 1, None, False, "THJ05511|THJ05531|THJ05532"),
    # no published rate: divide
    ("How many overnight stays per inhabitant were there in Iceland in 2025?",
     "How many overnight stays were there in Iceland in 2025?", 1, None, False, "divide"),
    ("How many overnight stays per inhabitant were there in Reykjavík in 2025?",
     "How many overnight stays were there in Reykjavík in 2025?", 1, "Reykjavík", False, "divide"),
    # the population date is named: divide even where a rate is published (it uses the average population)
    ("How many overnight stays in registered accommodation were there in Iceland in 2025 per inhabitant on 1 January 2025?",
     "How many overnight stays in registered accommodation were there in Iceland in 2025?", 1, None, True, "divide"),
    ("Hve margar gistinætur á skráðum gististöðum voru á Íslandi árið 2025 á hvern íbúa 1. janúar 2025?",
     "Hve margar gistinætur á skráðum gististöðum voru á Íslandi árið 2025?", 1, None, True, "divide"),
    ("How many children were born in Iceland in 2025 per 1,000 inhabitants on 1 January 2025?",
     "How many children were born in Iceland in 2025?", 1000, None, True, "divide"),
    ("Hve mörg börn fæddust á Íslandi árið 2025 á hverja 1.000 íbúa 1. janúar 2025?",
     "Hve mörg börn fæddust á Íslandi árið 2025?", 1000, None, True, "divide"),
    ("How many people died in Iceland in 2025 per 1,000 inhabitants on 1 January 2025?",
     "How many people died in Iceland in 2025?", 1000, None, True, "divide"),
    ("Hve margir létust á Íslandi árið 2025 á hverja 1.000 íbúa 1. janúar 2025?",
     "Hve margir létust á Íslandi árið 2025?", 1000, None, True, "divide"),
]
NOT_RATIOS = [
    "What was the estimated operational cost per student in primary schools in Iceland in 2025?",
    "How many people per household?",
    "How many people lived in Iceland on 1 January 2026?",
]


def main() -> None:
    catalog = Catalog.load(settings.catalog_path)
    print(f"catalog {settings.catalog_path.name}: searches {', '.join(['bm25', *(d.name for d in catalog.dense)])}\n")
    ok = 0
    for question, numerator, scale, place, dated, route in CASES:
        ask = detect_ratio(question)
        if not ask:
            print(f"FAIL not seen as a ratio: {question}")
            continue
        hit = stored_rate(catalog, ask, question)
        got_route = hit.id if hit else "divide"
        problems = [f"{name} {got!r} != {want!r}" for name, got, want in [
            ("numerator", ask.numerator, numerator), ("scale", ask.scale, scale), ("place", ask.place, place),
            ("dated", ask.dated, dated)] if got != want]
        if got_route not in route.split("|"):
            problems.append(f"route {got_route} not in {route}")
        ok += not problems
        print(f"{'ok  ' if not problems else 'FAIL'} {got_route:10} {question}")
        for pr in problems:
            print(f"       {pr}")
    for question in NOT_RATIOS:
        ask = detect_ratio(question)
        ok += ask is None
        print(f"{'ok  ' if ask is None else 'FAIL'} {'no ratio':10} {question}")
    print(f"\n{ok}/{len(CASES) + len(NOT_RATIOS)} passed")


if __name__ == "__main__":
    main()

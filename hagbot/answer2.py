"""An answer from one reading of the question (hagbot/reader.py): the figures fetched, computed and written in code.

The model's one call chose tables, options, a period, an operation and a shape; everything here is rules. Numbers
come only from the tables, every calculation is shown, and the text is a template in the question's language, so
it cannot say a number the data does not have. When the shape is not settled by the question and the data could be
shown more than one way, the user is asked first ("How would you like this shown?").
"""

from .catalog import Catalog
from .llm import LLM, Timer
from .reader import Family, Reading, candidates, dim, map_codes, option, periods, read
from .schemas import (Answer, AnswerPart, AskRequest, ChartPoint, ChartSpec, Clarification, ClarifyOption, Computation,
                      Fact, Row, Step, TableRef)
from .sources import table_url
from .steps.select import _total_of, ratio_measure, row_unit
from .text import fmt_number, fmt_period, fmt_value

MAX_LINES = 6
POPULATION = "MAN00101"      # Iceland's population on 1 January, by sex and age
MUNICIPAL_POPULATION = "MAN02005"


class Figure:
    """One pick fetched: its table, what was chosen, and its numbers as named series over periods."""

    def __init__(self, fam: Family, selections: dict[str, list[str]], rows: list[Row], label: str, lang: str):
        self.fam, self.meta, self.selections, self.rows, self.lang = fam, fam.meta, selections, rows, lang
        self.label = label or (fam.entry.title_is if lang == "is" and fam.entry.title_is else fam.entry.title_en)
        meta = fam.meta
        self.varying = [v for v in meta.variables if not v.time and len(selections.get(v.code, [])) > 1]
        self.places = next((map_codes(v) for v in self.varying if map_codes(v)), {})
        self.additive = not ratio_measure(meta, selections)
        self.series: dict[str, list[tuple[str, float]]] = {}
        self.codes: dict[str, str] = {}
        self.unit = ""
        for r in rows:
            if r.value is None:
                continue
            name = " · ".join(r.labels[v.text].strip() for v in self.varying) or self.label
            period = r.key.get(fam.time.code, "") if fam.time else ""
            self.series.setdefault(name, []).append((period, r.value))
            self.unit = self.unit or row_unit(meta, r.labels)
            for v in self.varying:
                if r.key.get(v.code) in self.places:
                    self.codes[name] = self.places[r.key[v.code]]
        for name, pts in self.series.items():
            pts.sort()
            first = next((i for i, (_, y) in enumerate(pts) if y != 0), 0)   # 0 before a category existed: missing
            self.series[name] = pts[first:] if first else pts

    def latest(self) -> dict[str, tuple[str, float]]:
        return {name: pts[-1] for name, pts in self.series.items() if pts}

    def ref(self) -> TableRef:
        e = self.fam.entry
        title = e.title_is if self.lang == "is" and e.title_is else e.title_en
        return TableRef(id=e.id, title=title, url=table_url(e.path, e.id), updated=e.last_period)


def _selections(fam: Family, pick, reading: Reading, display: str | None) -> tuple[dict[str, list[str]], list[str]]:
    """The model's choices for one pick as the table's own codes; anything it named that the table lacks is noted
    and left at the default."""
    meta, notes, out = fam.meta, [], {}
    for v in fam.measures:
        said = next((x for k, x in pick.measures.items() if dim(meta, k) is v), "")
        code = option(v, said) if said else None
        if said and not code:
            notes.append(f"{v.text}: {said!r} not an option")
        out[v.code] = [code or v.values[0]]
    for v in fam.groups:
        said = next((x for k, x in pick.groups.items() if dim(meta, k) is v), [])
        total = _total_of(v)
        if [s.strip().lower() for s in said] == ["all"]:
            out[v.code] = [c for c in v.values if c != total]
            continue
        codes = [c for c in (option(v, s) for s in said) if c]
        if said and len(codes) < len(said):
            notes.append(f"{v.text}: {len(said) - len(codes)} of {said} not options")
        out[v.code] = list(dict.fromkeys(codes)) or [total]
    for v in fam.fixed:
        out[v.code] = list(v.values)
    if fam.time:
        start, end, op = reading.period_from, reading.period_to, reading.operation
        if display == "line" and not start:
            start = "all"
        if display in ("bars", "ranking", "map") and op == "series":
            start, end, op = "", "", "value"
        out[fam.time.code] = periods(fam.time, start, end, op)
    return out, notes


def _display_question(fams_used: list[Figure], reading: Reading, lang: str) -> Clarification | None:
    """Several ways to show it and nothing in the question to choose by: the user picks."""
    if reading.shape_from_question or len(fams_used) != 1 or reading.operation not in ("value", "series"):
        return None
    f = fams_used[0]
    several = len(f.series) > 1
    over_time = f.fam.time is not None and len(f.fam.time.values) > 2
    if not (several and over_time):
        return None
    en = lang != "is"
    opts = [ClarifyOption(label="Latest numbers, side by side" if en else "Nýjustu tölur hlið við hlið", display="bars"),
            ClarifyOption(label="Development over time" if en else "Þróun yfir tíma", display="line")]
    if f.places:
        opts.append(ClarifyOption(label="On a map of Iceland" if en else "Á Íslandskorti", display="map"))
    return Clarification(message="How would you like this shown?" if en else "Hvernig viltu sjá svarið?", options=opts)


def _cap(series: dict[str, list], additive: bool, lang: str) -> dict[str, list]:
    """At most MAX_LINES lines: the largest by their latest value; the rest one "Other" line when they add up."""
    if len(series) <= MAX_LINES:
        return series
    ranked = sorted(series, key=lambda n: abs(series[n][-1][1]) if series[n] else 0, reverse=True)
    keep = ranked[:MAX_LINES - 1] if additive else ranked[:MAX_LINES]
    out = {n: series[n] for n in keep}
    if additive:
        rest, other = ranked[MAX_LINES - 1:], {}
        for n in rest:
            for p, y in series[n]:
                other[p] = other.get(p, 0.0) + y
        out[f"{'Annað' if lang == 'is' else 'Other'} ({len(rest)})"] = sorted(other.items())
    return out


def _views(time_axis: bool, n_series: int, monthly: bool, mappable: bool, parts_of_whole: bool) -> list[str]:
    if time_axis:
        v = ["line"] + (["stacked"] if n_series > 1 else []) + ["bars"]
        return v + (["heatmap"] if monthly and n_series == 1 else [])
    return ["bars", "lollipop"] + (["map"] if mappable else []) + (["donut"] if parts_of_whole else [])


def _fmt(x: float, unit: str, lang: str) -> str:
    return fmt_value(x, unit, lang) + ("" if unit == "%" else f" {unit}" if unit and unit != "%" else "")


async def answer(llm: LLM, catalog: Catalog, source, req: AskRequest, lang: str, steps: list[Step]) -> Answer | None:
    """The answer from one reading, or None when no candidate table has the figure (the old pipeline then answers)."""
    question = req.question
    t = Timer()
    fams = await candidates(catalog, source, question)
    steps.append(Step(name="find", ms=t.ms(), detail=" · ".join(f"{f.key} {f.meta.id}" for f in fams)))
    if not fams:
        return None
    t = Timer()
    reading = await read(llm, fams, question, req.display)
    steps.append(Step(name="read", ms=t.ms(), detail=reading.model_dump_json(exclude_defaults=True)))
    by_key = {f.key: f for f in fams} | {f.meta.id: f for f in fams}
    picks = [(by_key[p.table.strip()], p) for p in reading.picks if p.table.strip() in by_key]
    if reading.none_fit or not picks:
        return None

    t = Timer()
    figures, notes = [], []
    for fam, pick in picks:
        sel, n = _selections(fam, pick, reading, req.display)
        notes += n
        rows = await source.query(fam.meta.id, sel)
        figures.append(Figure(fam, sel, rows, pick.label, lang))
    steps.append(Step(name="fetch", ms=t.ms(), detail="; ".join(f"{f.meta.id}: {len(f.rows)} numbers" for f in figures)
                      + (f" · notes: {notes}" if notes else "")))
    figures = [f for f in figures if f.series]
    if not figures:
        return None

    if not req.display and (clar := _display_question(figures, reading, lang)):
        return Answer(question=question, language=lang, status="clarify", text=clar.message, clarification=clar)

    t = Timer()
    op = reading.operation
    shape = req.display or reading.shape
    parts, sentences, computation = [], [], None
    en = lang != "is"

    if op == "per_inhabitant" and figures:
        computation, sentence = await _per_inhabitant(source, figures[0], reading.per or 1, lang)
        if sentence:
            sentences.append(sentence)
    elif op == "share" and figures:
        computation, sentence = await _share(source, figures, lang)
        if sentence:
            sentences.append(sentence)
    elif op == "change":
        for f in figures:
            for name, pts in list(f.series.items())[:4]:
                if len(pts) < 2:
                    continue
                (p0, a), (p1, b) = pts[0], pts[-1]
                diff = b - a
                pct = f.unit != "%" and a and diff / a * 100
                sign = "+" if diff > 0 else ""
                change = f"{sign}{fmt_value(diff, f.unit, lang)}" + (f" ({sign}{fmt_number(pct, lang, 1)}%)" if pct else "")
                sentences.append(f"{name}: {_fmt(a, f.unit, lang)} ({fmt_period(p0, lang)}) → {_fmt(b, f.unit, lang)} "
                                 f"({fmt_period(p1, lang)}), {'breyting' if not en else 'a change of'} {change}.")
                computation = computation or Computation(op="difference", expression=f"{fmt_value(b, f.unit, lang)} − {fmt_value(a, f.unit, lang)}",
                                                         result=diff, unit=f.unit)
    elif op == "rank":
        f = figures[0]
        ranked = sorted(f.latest().items(), key=lambda kv: -kv[1][1])[: reading.top or 10]
        head = "Stærst" if not en else "Largest"
        sentences.append(f"{head} ({fmt_period(ranked[0][1][0], lang)}): " + "; ".join(
            f"{i}. {n} {_fmt(y, f.unit, lang)}" for i, (n, (_, y)) in enumerate(ranked, 1)) + ".")
        f.series = {n: [pt] for n, pt in ranked}
        shape = shape if shape not in ("auto", "line") else "ranking"
    elif op == "sum" and figures:
        f = figures[0]
        latest = f.latest()
        total = sum(y for _, y in latest.values())
        period = max(p for p, _ in latest.values())
        computation = Computation(op="sum", expression=" + ".join(fmt_value(y, f.unit, lang) for _, y in latest.values()),
                                  result=total, unit=f.unit)
        sentences.append(f"{' + '.join(latest)}: {_fmt(total, f.unit, lang)} ({fmt_period(period, lang)}).")
    else:   # value or series
        for f in figures:
            for name, pts in list(f.series.items())[:6]:
                (p0, a), (p1, b) = pts[0], pts[-1]
                if len(pts) > 2:
                    pk, top = max(pts, key=lambda x: x[1])
                    sentences.append(f"{name}: {_fmt(a, f.unit, lang)} ({fmt_period(p0, lang)}), {_fmt(b, f.unit, lang)} "
                                     f"({fmt_period(p1, lang)}); {'hæst' if not en else 'highest'} {_fmt(top, f.unit, lang)} ({fmt_period(pk, lang)}).")
                else:
                    sentences.append(f"{name}: {_fmt(b, f.unit, lang)} ({fmt_period(p1, lang)}).")
            if len(f.series) > 6:
                sentences.append(f"… {'og' if not en else 'and'} {len(f.series) - 6} {'fleiri' if not en else 'more'}.")

    for i, f in enumerate(figures):
        parts.append(_part(f, question, lang, shape, computation if i == 0 else None))
    note = ""
    if any(p.chart and "map" in ([p.chart.view] + p.chart.views) for p in parts):
        note = ("Kort: IS 50V Sveitarfélög, Náttúrufræðistofnun / Landmælingar Íslands, CC BY 4.0" if not en else
                "Map: IS 50V Sveitarfélög, Náttúrufræðistofnun / Landmælingar Íslands, CC BY 4.0")
        parts[0].note = note
    steps.append(Step(name="compute", ms=t.ms(), detail=f"{op}, shown as {shape}"
                      + (f"; {computation.expression} = {fmt_value(computation.result, computation.unit, lang)}" if computation else "")))
    lead = "Samkvæmt Hagstofu Íslands" if not en else "According to Statistics Iceland"
    text = f"{lead} ({', '.join(f.meta.id for f in figures)}): " + " ".join(sentences)
    return Answer(question=question, language=lang, status="answered", text=text, parts=parts, text_source="template",
                  sources=list({f.meta.id: f.ref() for f in figures}.values()))


def _part(f: Figure, question: str, lang: str, shape: str, computation: Computation | None) -> AnswerPart:
    time_axis = any(len(pts) > 1 for pts in f.series.values())
    # the line limit is for lines over time; bars, a ranking and a map show every category
    series = _cap(f.series, f.additive, lang) if time_axis else f.series
    facts = [Fact(label=f"{n}, {p}", value=y, unit=f.unit, formatted=fmt_value(y, f.unit, lang))
             for n, pts in list(series.items())[:6] for p, y in pts[-1:]]
    if computation:
        facts.append(Fact(label=computation.op, value=computation.result, unit=computation.unit,
                          formatted=fmt_value(computation.result, computation.unit, lang)))
    monthly = time_axis and all("M" in p for pts in series.values() for p, _ in pts)
    total_known = all(_total_of(v) is not None for v in f.varying)
    views = _views(time_axis, len(series), monthly, bool(f.codes) and not time_axis, f.additive and total_known and len(series) <= 7)
    view = {"number": "", "line": "line", "bars": "bars", "ranking": "bars", "stacked": "stacked", "map": "map",
            "heatmap": "heatmap", "table": "bars"}.get(shape, "")
    if view and view not in views:
        view = ""
    if time_axis:
        data = [ChartPoint(x=p, y=y, series=n if len(series) > 1 else None) for n, pts in series.items() for p, y in pts]
        kind = "line"
    else:
        data = [ChartPoint(x=n, y=pts[-1][1], code=f.codes.get(n)) for n, pts in series.items() if pts]
        kind = "kpi" if len(data) == 1 else "bar"
    if kind == "kpi":
        view, views = "", []
    chart = ChartSpec(type=kind, title=f.ref().title, x_label=f.fam.time.text if time_axis and f.fam.time else "",
                      unit=f.unit, data=data, view=view, views=[v for v in views if v != view])
    selection = {v.text: [v.label_of(c) for c in f.selections.get(v.code, [])][:12] for v in f.meta.variables}
    return AnswerPart(sub_question=question, table=f.ref(), selection=selection, rows=f.rows[:400], unit=f.unit,
                      computation=computation, facts=facts, chart=chart)


async def _population(source, year: str, place_number: str | None = None) -> float | None:
    """Iceland's population on 1 January of a year, or one municipality's (by its official number)."""
    try:
        if place_number:
            meta = await source.meta(MUNICIPAL_POPULATION)
            muni = next(v for v in meta.variables if not v.time and map_codes(v))
            code = next((c for c, n in map_codes(muni).items() if n == place_number), None)
            if code is None:
                return None
            sel = {v.code: ([code] if v is muni else [year] if v.time else [_total_of(v)]) for v in meta.variables}
            rows = await source.query(MUNICIPAL_POPULATION, sel)
        else:
            meta = await source.meta(POPULATION)
            sel = {v.code: ([year] if v.time else [_total_of(v)]) for v in meta.variables}
            rows = await source.query(POPULATION, sel)
        return next((r.value for r in rows if r.value), None)
    except Exception:
        return None


async def _per_inhabitant(source, f: Figure, per: int, lang: str):
    name, (period, value) = next(iter(f.latest().items()))
    pop = await _population(source, period[:4], f.codes.get(name))
    if not pop:
        return None, ""
    per = per if per in (1, 1000, 100, 100000) else 1
    result = value / pop * per
    unit = f"per {fmt_number(per, lang)} inhabitants" if per > 1 else "per inhabitant"
    if lang == "is":
        unit = f"á hverja {fmt_number(per, lang)} íbúa" if per > 1 else "á hvern íbúa"
    expr = f"{fmt_number(value, lang)} / {fmt_number(pop, lang)}" + (f" × {fmt_number(per, lang)}" if per > 1 else "")
    sentence = f"{name}: {fmt_number(result, lang)} {unit} ({fmt_period(period, lang)}; {expr})."
    return Computation(op="ratio", expression=expr, result=result, unit=unit), sentence


async def _share(source, figures: list[Figure], lang: str):
    """The first figure as a share of the second, or of its own total when there is one figure."""
    f = figures[0]
    name, (period, part) = next(iter(f.latest().items()))
    if len(figures) > 1:
        wname, (_, whole) = next(iter(figures[1].latest().items()))
    else:
        named = [v for v in f.meta.variables if not v.time and _total_of(v) is not None
                 and f.selections.get(v.code) and f.selections[v.code] != [_total_of(v)]]
        if not named:
            return None, ""
        sel = {**f.selections, named[0].code: [_total_of(named[0])]}
        if f.fam.time:
            sel[f.fam.time.code] = [period]
        rows = await source.query(f.meta.id, sel)
        whole = next((r.value for r in rows if r.value), None)
        wname = named[0].label_of(_total_of(named[0]))
    if not whole:
        return None, ""
    pct = part / whole * 100
    expr = f"{fmt_number(part, lang)} / {fmt_number(whole, lang)} × 100"
    sentence = (f"{name} {'er' if lang == 'is' else 'is'} {fmt_number(pct, lang, 1)}% "
                f"{'af' if lang == 'is' else 'of'} {wname} ({fmt_period(period, lang)}; {expr}).")
    return Computation(op="share", expression=expr, result=pct, unit="%"), sentence

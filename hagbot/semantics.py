"""Build source metadata profiles and validate compatibility before requesting cells.

Model-written catalog descriptions and dictionary matches are retrieval hints, never source evidence.
"""

import hashlib
import json
import re

from pydantic import BaseModel

from .outcomes import AnswerFailure
from .catalog import _same_word
from .sources import TableMeta, table_url
from .specification import Compatibility, Evidence, QuestionSpec, Requirement, TableVerification
from .text import normalize, tokens


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class TableProfile(BaseModel):
    table_id: str
    source_url: str
    metadata_sha256: str
    fields: dict[str, str]
    unit_hint: str = ""  # catalog unit, when the source did not declare one; cannot be cited


def profile(meta: TableMeta) -> TableProfile:
    fields = {"title": meta.title} if meta.title_source == "source" else {}
    if meta.description:
        fields["description"] = meta.description
    if meta.source:
        fields["source"] = meta.source
    fields.update({f"note:{i}": note for i, note in enumerate(meta.notes)})
    if meta.unit and meta.unit_source == "source":
        fields["unit"] = meta.unit
    for var in meta.variables:
        fields[f"dimension:{var.code}"] = var.text
        for code, label in zip(var.values, var.value_texts):
            fields[f"value:{var.code}:{code}"] = label
        if var.time:
            kinds = {"monthly" if re.fullmatch(r"\d{4}M\d{2}", c) else
                     "quarterly" if re.fullmatch(r"\d{4}Q[1-4]", c) else
                     "annual" if re.fullmatch(r"\d{4}", c) else "unknown" for c in var.values}
            if len(kinds) == 1 and "unknown" not in kinds:
                fields[f"frequency:{var.code}"] = next(iter(kinds))
    return TableProfile(table_id=meta.id, source_url=table_url(meta.path, meta.id),
                        metadata_sha256=fingerprint(meta.model_dump()), fields=fields,
                        unit_hint=meta.unit if meta.unit_source != "source" else "")


def validate_specification(spec: QuestionSpec, question: str, context: str = "") -> None:
    if spec.ambiguities:
        raise AnswerFailure("clarify", "ambiguous_specification", "; ".join(spec.ambiguities))
    if sum(r.facet == "measure" for r in spec.requirements) != 1:
        raise AnswerFailure("unverified", "invalid_specification", "exactly one measure is required")
    defaults = {"geography": {"iceland"}, "period": {"latest"}, "adjustment": {"unadjusted"}, "statistic": {"total"}}
    for req in spec.requirements:
        if req.origin == "default":
            if req.quote or normalize(req.value) not in defaults.get(req.facet, set()):
                raise AnswerFailure("unverified", "unsupported_default", f"{req.facet}: {req.value}")
            continue
        available = question if req.origin == "user" else f"{question} {context}"
        quote = " ".join(tokens(req.quote))
        if not quote or f" {quote} " not in f" {' '.join(tokens(available))} ":
            raise AnswerFailure("unverified", "ungrounded_specification", req.quote)
        if req.origin == "user" and req.facet == "population":
            if not set(re.findall(r"\d+", req.value)) <= set(re.findall(r"\d+", req.quote)):
                raise AnswerFailure("unverified", "invented_population_threshold", req.value)


# These are statistical distinctions, not table-ID mappings. Selected measure labels take precedence over a title
# that lists several alternatives. No assertion is made from the absence of an adjustment/definition.
_AXES = {
    "measure": {
        "wealth": r"\bwealth\b|\bnet worth\b|\beign\w*|\bau[dr]ur\b",
        "income": r"\bincome\b|\btekj\w*",
        "wages": r"\bwages?\b|\bearnings\b|\blaun\w*",
        "population": r"\bpopulation\b|\bmannfjold\w*",
        "unemployment": r"\bunemployment\b|\batvinnuleys\w*",
        "employment": r"(?<!un)\bemployment\b|\bstarfandi\b",
        "consumer price index": r"\bconsumer price index\b|\bcpi\b|\bvisital\w* neysluverd\w*",
        "inflation": r"\binflation\b|\bverdbolg\w*",
    },
    "statistic": {"median": r"\bmedian\b|\bmidgild\w*", "mean": r"\bmean\b|\baverage\b|\bmedaltal\w*",
                  "annual change": r"\bannual (?:change|growth|rate of change)\b|\b12.month|\byear.on.year\b|"
                                   r"\bchange (?:on|from) (?:the )?previous year\b|\barsbreyt\w*",
                  "index": r"\bindex\b|\bvisital\w*"},
    "price_basis": {"constant prices": r"\bconstant prices?\b|\breal terms\b|\bfast\w* verdlag\w*",
                    "current prices": r"\bcurrent prices?\b|\bnominal\b|\bverdlag\w* hvers ars\b"},
    "tax_basis": {"gross": r"\bgross\b|\bbefore tax\b|\bfyrir skatt\w*",
                  "net": r"\bnet\b|\bafter tax\b|\beftir skatt\w*"},
    "adjustment": {"seasonally adjusted": r"\bseasonally adjusted\b|\barstidaleidr\w*",
                   "unadjusted": r"\bunadjusted\b|\bnot seasonally adjusted\b|\boarstidaleidr\w*"},
}


def _tags(facet: str, text: str) -> set[str]:
    text = normalize(text)
    if facet == "adjustment":
        text = text.replace("not seasonally adjusted", "unadjusted")
    tags = {value for value, pattern in _AXES.get(facet, {}).items() if re.search(pattern, text)}
    if facet == "statistic" and "annual change" in tags:
        tags.discard("index")  # "consumer price index, annual change" names a rate, not an index level
    if facet == "measure" and "consumer price index" in tags and "annual change" in _tags("statistic", text):
        tags.add("inflation")
    return tags


def _unit(text: str) -> tuple[str, int] | None:
    text = normalize(text)
    choices = {"percent": r"(?<![\d.])%|\bpercent\w*|\bprosent\w*",
               "ISK": r"\bisk\b|\b(?:icelandic )?kron\w*",
               "EUR": r"\beur\b|\beuros?\b", "USD": r"\busd\b|\bus dollars?\b",
               "persons": r"\bpersons?\b|\bpeople\b|\binhabitants?\b|\bibuar\b|\beinstakling\w*",
               "index": r"\bindex\b|\bvisital\w*"}
    found = {name for name, pattern in choices.items() if re.search(pattern, text)}
    if len(found) != 1:
        return None
    scale = 10**9 if re.search(r"\bbillion\w*|\bmilljard\w*", text) else \
        10**6 if re.search(r"\bmillion\w*|\bmilljon\w*", text) else \
        1000 if re.search(r"\bthousand\w*|\bthusund\w*", text) else 1
    return next(iter(found)), scale


def scope_evidence(meta: TableMeta, req: Requirement,
                   selections: dict[str, list[str]] | None = None) -> Evidence | None:
    """A named national survey can establish scope without a country filter.

    The publisher's name alone (Statistics Iceland) is deliberately insufficient:
    it also publishes international comparisons. A country/area dimension must
    instead be checked against its selected option in the ordinary path.
    """
    if req.facet != "geography":
        return None
    geographic = [v for v in meta.variables if re.search(
        r"country|municipal|region|area|town|sveitarfel|landshlut|domicile|logheimil", normalize(v.text))
        and not re.search(r"birth|citizen|origin|rikisfang|faeding|uppruni", normalize(v.text))]
    names = tokens(req.value)
    if len(names) == 1 and len(names[0]) >= 4:
        for var in geographic:
            matches = [code for code, label in zip(var.values, var.value_texts)
                       if any(_same_word(word, names[0]) for word in tokens(label) if len(word) >= 4)]
            if len(matches) == 1 and matches[0] in (selections or {}).get(var.code, []):
                return Evidence(field=f"value:{var.code}:{matches[0]}", quote=var.label_of(matches[0]))
    if (normalize(req.value) == "iceland" and not geographic
            and re.fullmatch(r"icelandic labo[u]?r force survey", normalize(meta.source).strip())):
        return Evidence(field="source", quote=meta.source)
    return None


def validate_compatibility(meta: TableMeta, selections: dict[str, list[str]], spec: QuestionSpec,
                           proof: Compatibility) -> TableVerification:
    p = profile(meta)
    # Citation of an available but unselected category would prove a different query.
    allowed = {name: value for name, value in p.fields.items() if not name.startswith("value:")}
    selected_labels = []
    for var in meta.variables:
        for code in selections[var.code]:
            key = f"value:{var.code}:{code}"
            allowed[key] = p.fields[key]
            if not var.time:
                selected_labels.append(p.fields[key])
    indices = [check.requirement for check in proof.checks]
    if sorted(indices) != list(range(len(spec.requirements))):
        raise AnswerFailure("unverified", "incomplete_compatibility", str(indices))
    desired = {facet: set().union(*(_tags(facet, r.value) for r in spec.requirements if r.facet == facet))
               for facet in _AXES}
    checks = []
    for check in proof.checks:
        check = check.model_copy(deep=True)
        req = spec.requirements[check.requirement]
        for ref in check.evidence:
            quote = " ".join(tokens(ref.quote))
            if ref.field not in allowed or not quote or f" {quote} " not in f" {' '.join(tokens(allowed[ref.field]))} ":
                raise AnswerFailure("unverified", "invalid_metadata_evidence", ref.field)
        scope = scope_evidence(meta, req, selections)
        if scope is not None:
            # The selected geographic option or explicit national survey scope
            # is code-verifiable, regardless of the model's vote.
            check.verdict = "supported"
            if scope not in check.evidence:
                check.evidence.append(scope)
        elif check.verdict == "unknown" or not check.evidence:
            raise AnswerFailure("unverified", "unknown_compatibility", f"{req.facet}: {req.value}")
        elif check.verdict == "unsupported":
            raise AnswerFailure("unavailable", "incompatible_table", f"{req.facet}: {req.value}")
        wanted = _tags(req.facet, req.value)
        selected = set().union(*(_tags(req.facet, label) for label in selected_labels))
        actual = selected or _tags(req.facet, " ".join(p.fields[k] for k in ("title", "description") if k in p.fields))
        if req.facet == "measure" and "consumer price index" in actual and "annual change" in _tags("statistic", " ".join(selected_labels)):
            actual.add("inflation")
        if wanted and actual and wanted.isdisjoint(actual):
            raise AnswerFailure("unavailable", "incompatible_definition", f"{req.facet}: {req.value} vs {sorted(actual)}")
        extra = actual - desired.get(req.facet, set())
        if req.facet == "measure" and "inflation" in desired["measure"]:
            extra.discard("consumer price index")  # inflation is a particular transformation of the CPI
            stats = set().union(*(_tags("statistic", label) for label in selected_labels))
            if "index" in stats:
                raise AnswerFailure("unavailable", "incompatible_definition", "CPI level selected for inflation")
        if wanted and extra:
            raise AnswerFailure("unavailable", "incompatible_definition", f"unrequested {req.facet}: {sorted(extra)}")
        # A median/real/net label must actually be present in the cited fields, not merely somewhere in the table.
        cited = " ".join(allowed[ref.field] for ref in check.evidence)
        if req.facet == "measure" and wanted and not wanted <= _tags("measure", cited):
            # The code already checked the selected query's measure above. When
            # the model cites a real but unhelpful field (e.g. a town label for
            # population), an unambiguous source title/description can provide
            # the missing evidence. Never repair fabricated citations, unknown
            # verdicts, or contradictory selected measures.
            for field in ("title", "description"):
                text = allowed.get(field, "")
                if _tags("measure", text) == wanted:
                    check.evidence.append(Evidence(field=field, quote=text))
                    cited += " " + text
                    break
        if wanted and not wanted <= _tags(req.facet, cited):
            raise AnswerFailure("unverified", "insufficient_metadata_evidence", f"{req.facet}: {req.value}")
        if req.facet in ("population", "geography", "period"):
            numbers = set(re.findall(r"\d+", req.value))
            if not numbers <= set(re.findall(r"\d+", cited)):
                raise AnswerFailure("unverified", "insufficient_metadata_evidence", req.value)
        if req.facet == "geography":
            names = [w for w in tokens(req.value) if len(w) >= 4]
            cited_words = tokens(cited)
            literal = names and all(any(a.startswith(b) or b.startswith(a) for b in cited_words if len(b) >= 4) for a in names)
            if not literal and scope is None:
                raise AnswerFailure("unverified", "insufficient_metadata_evidence", req.value)
        if req.facet == "unit":
            wanted_unit = _unit(req.value)
            measures = [var.label_of(code) for var in meta.variables if
                        re.search(r"unit|measure|contents|eining|maelikvard", normalize(var.text))
                        for code in selections[var.code]]
            actual_unit = _unit(" ".join(measures)) or _unit(allowed.get("unit", "")) or _unit(allowed.get("title", ""))
            if wanted_unit and actual_unit and wanted_unit != actual_unit:
                raise AnswerFailure("unavailable", "incompatible_unit", f"{wanted_unit} vs {actual_unit}")
            if not wanted_unit or _unit(cited) != wanted_unit:
                raise AnswerFailure("unverified", "insufficient_metadata_evidence", req.value)
        if req.facet == "frequency":
            offered = {v for k, v in allowed.items() if k.startswith("frequency:")}
            if normalize(req.value) not in offered:
                raise AnswerFailure("unavailable", "incompatible_frequency", req.value)
        checks.append(check)
    return TableVerification(table_id=meta.id, metadata_sha256=p.metadata_sha256,
                             question_sha256=fingerprint(spec.model_dump()), selection_sha256=fingerprint(selections),
                             executed_selection_sha256=fingerprint(selections), specification=spec, checks=checks)

"""The answer contract between backend and frontend. The frontend renders only from these objects."""

from typing import Literal

from pydantic import BaseModel, Field

Language = Literal["en", "is"]


class Turn(BaseModel):
    """An earlier question and its answer, sent back by the browser so follow-ups can refer to it."""
    question: str
    answer: str = ""
    tables: list[str] = []


class AskRequest(BaseModel):
    question: str
    language: Language | None = None  # None: detect from the question
    table_id: str | None = None  # set when the user picked a table in a clarification
    history: list[Turn] = []  # the conversation so far, oldest first (the browser keeps the last few)
    model: str | None = None  # one of /api/models; None: the server's default, the best one we have measured
    temperature: float | None = None  # None: 0, the same answer every time
    reasoning: Literal["none", "low", "medium", "high"] | None = None  # thinking length; None: the server's default
    fast: bool = False  # "Answer now": the fast model with thinking off, whatever else is asked


class TableRef(BaseModel):
    id: str
    title: str
    url: str  # human-readable page on px.hagstofa.is


class Row(BaseModel):
    """One cell of a table, with codes and labels for every dimension."""

    key: dict[str, str]  # dimension code -> value code
    labels: dict[str, str]  # dimension label -> value label
    value: float | None


class Computation(BaseModel):
    op: str
    expression: str  # e.g. "139,804 − 138,772"
    result: float
    unit: str


class Fact(BaseModel):
    label: str  # e.g. "Population, Reykjavík, 2026"
    value: float
    unit: str
    formatted: str  # value formatted for the answer language


class ChartPoint(BaseModel):
    x: str
    y: float
    series: str | None = None


class ChartSpec(BaseModel):
    """Renderer-agnostic chart description. The frontend maps it onto Vega-Lite."""

    type: Literal["kpi", "line", "bar"]
    title: str
    x_label: str = ""
    y_label: str = ""
    unit: str = ""
    data: list[ChartPoint] = Field(default_factory=list)


class AnswerPart(BaseModel):
    """The answer to one sub-question, from one table."""

    sub_question: str
    table: TableRef
    selection: dict[str, list[str]]  # dimension label -> chosen value labels
    rows: list[Row]
    unit: str
    computation: Computation | None = None
    facts: list[Fact]
    chart: ChartSpec | None = None
    note: str = ""  # e.g. why the latest figure is from an earlier year, and where newer figures are
    ranking: list[dict] = []  # the ranked hitlist the table was chosen from: id, title, score, signals


class ClarifyOption(BaseModel):
    label: str
    table_id: str


class Clarification(BaseModel):
    message: str
    options: list[ClarifyOption]


class Step(BaseModel):
    name: str
    ms: int
    detail: str = ""


class Answer(BaseModel):
    question: str
    language: Language
    status: Literal["answered", "clarify", "error"]
    text: str
    parts: list[AnswerPart] = Field(default_factory=list)
    clarification: Clarification | None = None
    sources: list[TableRef] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    # "model": the model's sentence passed the number check; "template": it failed or errored, facts restated
    text_source: Literal["model", "template", "none"] = "none"
    llm_calls: int = 0
    total_ms: int = 0
    model: str = ""


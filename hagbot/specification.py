"""Contracts for a question's meaning and the metadata evidence used to verify it."""

from typing import Literal

from pydantic import BaseModel, Field

Facet = Literal["measure", "statistic", "population", "geography", "period", "unit", "frequency",
                "adjustment", "price_basis", "tax_basis"]


class Requirement(BaseModel):
    facet: Facet
    value: str = Field(min_length=1, description="Canonical English meaning, e.g. net wealth, median, constant prices")
    origin: Literal["user", "context", "default"]
    quote: str = Field(description="Exact quote from the question or supplied context; empty for a default")


class QuestionSpec(BaseModel):
    requirements: list[Requirement] = Field(min_length=1, description=(
        "Exactly one measure, then every required statistical definition and filter. "
        "Keep income/wealth, mean/median, gross/net, current/constant prices distinct."))
    operation: Literal["value", "series", "difference", "pct_change", "share", "sum", "max", "min", "rank"]
    ambiguities: list[str] = Field(default_factory=list, description="Unresolved interpretations; never guess one")


class Evidence(BaseModel):
    field: str = Field(description="Exact metadata field ID from the profile")
    quote: str = Field(min_length=1, description="Exact supporting text copied from that field")


class RequirementCheck(BaseModel):
    requirement: int = Field(ge=0, description="Zero-based requirement index in the question specification")
    verdict: Literal["supported", "unsupported", "unknown"]
    evidence: list[Evidence] = Field(default_factory=list)


class Compatibility(BaseModel):
    checks: list[RequirementCheck]


class TableVerification(BaseModel):
    table_id: str
    metadata_sha256: str
    question_sha256: str
    selection_sha256: str
    executed_selection_sha256: str
    specification: QuestionSpec
    amendments: list[str] = Field(default_factory=list)
    checks: list[RequirementCheck]

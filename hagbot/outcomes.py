"""Expected abstentions, separate from failures of the model or data service."""

from .schemas import FailureStatus, TableAttempt

# Only evidence/coverage failures justify looking in another table. Malformed model output, ambiguity and
# infrastructure failures stop the request rather than turning a retry into a weaker interpretation.
RETRYABLE_TABLE_FAILURES = frozenset({
    "incompatible_table", "incompatible_definition", "incompatible_unit", "incompatible_frequency",
    "requested_filter_unavailable", "requested_period_unavailable", "requested_base_unavailable",
    "requested_window_unavailable", "empty_dimension", "unknown_compatibility", "insufficient_metadata_evidence",
})


MESSAGES = {
    "clarify": (
        "Please specify the measure or filter you mean so I can answer accurately.",
        "Vinsamlegast tilgreindu mælikvarðann eða afmörkunina svo ég geti svarað rétt.",
    ),
    "out_of_scope": (
        "This request is outside the supported scope: lookups and supported calculations using Statistics Iceland's published tables.",
        "Þessi beiðni er utan þess sem kerfið styður: uppflettingar og studdir útreikningar úr útgefnum töflum Hagstofu Íslands.",
    ),
    "unavailable": (
        "The requested data or calculation is not available from the verified table. I have not substituted another period or category.",
        "Umbeðin gögn eða útreikningur eru ekki tiltæk í töflunni sem var skoðuð. Ég hef ekki skipt út tímabili eða flokki.",
    ),
    "unverified": (
        "I could not verify a table and query that answer this question accurately. This does not establish that the data does not exist.",
        "Ég gat ekki staðfest töflu og fyrirspurn sem svara spurningunni rétt. Það þýðir ekki að gögnin séu ekki til.",
    ),
    "error": (
        "A technical problem prevented me from completing the answer. Please try again.",
        "Tæknilegt vandamál kom í veg fyrir að ég gæti lokið svarinu. Reyndu aftur.",
    ),
}


class AnswerFailure(Exception):
    """An expected, typed reason to stop before generating an answer.

    Detail is for the trace; the public message never treats a retrieval miss as proof of nonexistence.
    """

    def __init__(self, status: FailureStatus, code: str, detail: str = "", *, attempts: list[TableAttempt] | None = None):
        self.status, self.code, self.detail = status, code, detail
        self.attempts = list(attempts or [])
        super().__init__(f"{code}: {detail}" if detail else code)

    def message(self, lang: str) -> str:
        if self.code == "missing_filter":
            return (f"Hvaða gildi á að nota fyrir „{self.detail}“?" if lang == "is" else
                    f"Which value should I use for “{self.detail}”?")
        if self.code == "requested_filter_unavailable":
            return (f"Taflan sem var skoðuð getur ekki afmarkað gögnin við „{self.detail}“." if lang == "is" else
                    f"The checked table cannot represent the requested filter “{self.detail}”.")
        return MESSAGES[self.status][lang == "is"]

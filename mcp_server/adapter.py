"""Pure API-to-MCP answer adaptation, without an MCP SDK dependency."""

def _answer(a: dict, max_rows: int = 60) -> dict:
    """The answer as a model reads it best: the sentence, the figures behind it, and where each came from."""
    out: dict = {"status": a["status"], "answer": a["text"], "language": a["language"],
                 "sources": [{"table_id": s["id"], "title": s["title"], "url": s["url"],
                              "source_kind": s.get("source_kind", "unknown"), "snapshot_at": s.get("snapshot_at", ""),
                              "retrieved_at": s.get("retrieved_at", "")} for s in (a.get("sources", []) if a["status"] == "answered" else [])]}
    if a.get("issues"):
        out["issues"] = a["issues"]
    if a["status"] == "clarify" and a.get("clarification"):
        c = a["clarification"]
        out["clarification"] = {"message": c.get("message", ""),
                                "options": [{"label": o["label"], "table_id": o["table_id"]} for o in c.get("options", [])],
                                "how_to_continue": "Ask again with the table_id of the option that fits."}
    parts = []
    for p in a.get("parts", []) if a["status"] == "answered" else []:
        dims = list((p["rows"][0]["labels"] if p["rows"] else {}).keys())
        part = {"sub_question": p["sub_question"], "table_id": p["table"]["id"], "unit": p["unit"],
                "selection": p["selection"],
                "figures": [{"label": f["label"], "value": f["value"], "formatted": f["formatted"], "unit": f["unit"]}
                            for f in p["facts"]],
                "rows": [{**{d: r["labels"].get(d) for d in dims}, "value": r["value"]} for r in p["rows"][:max_rows]]}
        if len(p["rows"]) > max_rows:
            part["rows_omitted"] = len(p["rows"]) - max_rows
        if p.get("computation"):
            part["computation"] = p["computation"]
        if p.get("note"):
            part["note"] = p["note"]
        if p.get("context"):
            part["context"] = p["context"]
        if p.get("verifications"):
            part["verifications"] = p["verifications"]
        if p.get("attempts"):
            part["attempts"] = p["attempts"]
        parts.append(part)
    if parts:
        out["parts"] = parts
    out["claims"] = a.get("claims", []) if a["status"] == "answered" else []
    out["numbers_checked"] = a["status"] == "answered" and (bool(a.get("claims")) or a.get("text_source") == "model")
    out["seconds"] = round(a.get("total_ms", 0) / 1000, 1)
    return out


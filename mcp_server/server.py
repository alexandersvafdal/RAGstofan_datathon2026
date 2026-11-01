"""RAGstofan as an MCP server, so Claude, ChatGPT and other MCP clients can ask Statistics Iceland questions.

It talks to a running RAGstofan API (RAGSTOFAN_URL, the Spark by default) and needs only httpx and mcp.

    # Claude Desktop / Claude Code, on your own machine (stdio)
    python mcp_server/server.py

    # remote connectors (ChatGPT, claude.ai): Streamable HTTP at http://<host>:8002/mcp
    python mcp_server/server.py --http --port 8002
"""

import argparse
import os

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

API = os.getenv("RAGSTOFAN_URL", "http://ragstofan-spark.tail4f2a97.ts.net:8000").rstrip("/")
ASK_TIMEOUT = float(os.getenv("RAGSTOFAN_TIMEOUT", "300"))   # a long question takes one to two minutes
MAX_ROWS = 60   # data rows passed back per table; the model cites these, the full table is a link away

server = MCPServer(
    name="RAGstofan",
    title="RAGstofan – Statistics Iceland",
    version="1.0.0",
    website_url="https://px.hagstofa.is",
    instructions=(
        "Official statistics from Statistics Iceland (Hagstofa Íslands): population, prices and inflation, wages, "
        "labour, housing, tourism, trade, GDP and more, in English or Icelandic. Use ask_icelandic_statistics for any "
        "question that needs Icelandic figures; it picks the table, reads the numbers and checks them. Quote the "
        "figures and cite the table id and link it returns. Use search_tables and describe_table to explore what "
        "data exists, then pass a table_id to ask_icelandic_statistics to answer from that table."
    ),
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)


async def _get(path: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{API}{path}", params=params)
        r.raise_for_status()
        return r.json()


def _answer(a: dict) -> dict:
    """The answer as a model reads it best: the sentence, the figures behind it, and where each came from."""
    out: dict = {"status": a["status"], "answer": a["text"], "language": a["language"],
                 "sources": [{"table_id": s["id"], "title": s["title"], "url": s["url"]} for s in a.get("sources", [])]}
    if a.get("issues"):
        out["issues"] = a["issues"]
    if a["status"] == "clarify" and a.get("clarification"):
        c = a["clarification"]
        out["clarification"] = {"message": c.get("message", ""),
                                "options": [{"label": o["label"], "table_id": o["table_id"]} for o in c.get("options", [])],
                                "how_to_continue": "Ask again with the table_id of the option that fits."}
    parts = []
    for p in a.get("parts", []):
        dims = list((p["rows"][0]["labels"] if p["rows"] else {}).keys())
        part = {"sub_question": p["sub_question"], "table_id": p["table"]["id"], "unit": p["unit"],
                "selection": p["selection"],
                "figures": [{"label": f["label"], "value": f["value"], "formatted": f["formatted"], "unit": f["unit"]}
                            for f in p["facts"]],
                "rows": [{**{d: r["labels"].get(d) for d in dims}, "value": r["value"]} for r in p["rows"][:MAX_ROWS]]}
        if len(p["rows"]) > MAX_ROWS:
            part["rows_omitted"] = len(p["rows"]) - MAX_ROWS
        if p.get("computation"):
            part["computation"] = p["computation"]
        if p.get("note"):
            part["note"] = p["note"]
        parts.append(part)
    if parts:
        out["parts"] = parts
    out["numbers_checked"] = a.get("text_source") == "model"   # every number in the sentence matched the table
    out["seconds"] = round(a.get("total_ms", 0) / 1000, 1)
    return out


@server.tool(annotations=READ_ONLY)
async def ask_icelandic_statistics(question: str, table_id: str | None = None, fast: bool = False) -> dict:
    """Answer a question about Iceland from Statistics Iceland's official tables, in English or Icelandic.

    Finds the right table, reads the figures, does any arithmetic (differences, shares, growth) and checks every
    number in the answer against the table. Several questions in one are split and answered together.
    Takes 20–120 seconds.

    Args:
        question: The question, e.g. "How many people live in Reykjavík?" or "Hver var verðbólgan í september?"
        table_id: Answer from this table (an id from search_tables or a clarification option) instead of searching.
        fast: Use the smaller, faster model. Quicker but less reliable.
    """
    body = {"question": question, "table_id": table_id, "fast": fast}
    async with httpx.AsyncClient(timeout=ASK_TIMEOUT) as c:
        r = await c.post(f"{API}/api/ask", json={k: v for k, v in body.items() if v not in (None, False)})
        r.raise_for_status()
        return _answer(r.json())


@server.tool(annotations=READ_ONLY)
async def search_tables(query: str, limit: int = 8) -> dict:
    """Search Statistics Iceland's 1,857 tables by topic, in English or Icelandic.

    Returns each table's id, title, dimensions, unit and the period it covers, best match first.

    Args:
        query: What to look for, e.g. "unemployment by age" or "gistinætur".
        limit: How many tables to return (1–25).
    """
    return await _get("/api/tables", q=query, limit=limit)


@server.tool(annotations=READ_ONLY)
async def describe_table(table_id: str) -> dict:
    """One table's details: title, description, dimensions, the period it covers and its category values
    (municipalities, age groups, industries…), so you can tell whether it holds what you need.

    Args:
        table_id: A table id such as "MAN00101" or "VIS01000".
    """
    return await _get(f"/api/tables/{table_id}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8002)
    args = ap.parse_args()
    if not args.http:
        server.run("stdio")
        return
    # Behind `tailscale serve` or a tunnel the Host header is the public name, so allow the names we are served under
    hosts = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=bool(hosts), allowed_hosts=hosts,
                                         allowed_origins=[f"https://{h}" for h in hosts])
    server.run("streamable-http", host=args.host, port=args.port, transport_security=security)


if __name__ == "__main__":
    main()

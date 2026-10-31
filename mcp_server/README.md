# RAGstofan MCP server

Lets Claude, ChatGPT and other MCP clients answer questions from Statistics Iceland through RAGstofan. It calls a
running RAGstofan API (the Spark by default) and needs only `httpx` and `mcp` (`pip install "mcp>=2.3" httpx`).

| Tool | What it does |
|---|---|
| `ask_icelandic_statistics(question, table_id?, fast?)` | Full answer: sentence, figures, data rows, calculation, sources with links. 20–120 s. |
| `search_tables(query, limit?)` | Best-matching tables of the 1,857, with dimensions and periods. |
| `describe_table(table_id)` | One table's dimensions, period and category values. |

`RAGSTOFAN_URL` picks the API (default `http://ragstofan-spark.tail4f2a97.ts.net:8000`, which needs Tailscale on your
machine). Use `http://localhost:8000` when the server runs locally.

## Claude Code

```bash
claude mcp add ragstofan -e RAGSTOFAN_URL=http://ragstofan-spark.tail4f2a97.ts.net:8000 -- /path/to/datathon2026/.venv/bin/python /path/to/datathon2026/mcp_server/server.py
```

## Claude Desktop

Settings → Developer → Edit Config, then add to `claude_desktop_config.json` and restart:

```json
{
  "mcpServers": {
    "ragstofan": {
      "command": "/path/to/datathon2026/.venv/bin/python",
      "args": ["/path/to/datathon2026/mcp_server/server.py"],
      "env": { "RAGSTOFAN_URL": "http://ragstofan-spark.tail4f2a97.ts.net:8000" }
    }
  }
}
```

## ChatGPT and claude.ai (remote connectors)

These run in the cloud, so they need a public HTTPS address; a Tailscale-only address will not work.

1. On the Spark, serve over HTTP: `MCP_ALLOWED_HOSTS=ragstofan-spark.tail4f2a97.ts.net python mcp_server/server.py --http --port 8002`
2. Make it public with Tailscale Funnel: `tailscale funnel --bg --set-path /mcp http://127.0.0.1:8002/mcp`
   (Funnel must be allowed for the tailnet in the Tailscale admin console.)
3. Add `https://ragstofan-spark.tail4f2a97.ts.net/mcp` as the connector URL:
   - ChatGPT: Settings → Apps & Connectors → Advanced → Developer mode, then Create, with no authentication.
   - claude.ai: Settings → Connectors → Add custom connector.

There is no login: anyone with the URL can ask questions and use the Spark's GPU. Turn it off with
`tailscale funnel --set-path /mcp off`.

## Try it without a chat app

```bash
npx @modelcontextprotocol/inspector .venv/bin/python mcp_server/server.py
```

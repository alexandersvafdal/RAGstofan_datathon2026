#!/bin/bash
# The language model on one JURECA GPU node for 30 minutes, for RAGstofan running on your own computer.
# Submitted by scripts/jureca.sh (sbatch reads this file from standard input), not by hand. The script passes the
# budget account, the log file and RAG_DIR (the folder holding Ollama and the models), so no account name is in here.
#
# Who can reach the model: JURECA does not let anyone SSH into a compute node, so the login node has to reach it over
# JURECA's internal network, which other JURECA users share. So Ollama listens on the node's loopback only, and a
# small gate in front of it lets a connection through only when its first request carries this job's secret token
# (Authorization: Bearer ..., which RAGstofan sends as LLM_API_KEY). The token is made fresh for each job and kept in a
# file only you can read. The node is not shared with other jobs (--exclusive), so all 4 GPUs (160 GB) are yours at no
# extra cost: a model too big for one GPU (gpt-oss:120b, qwen3:235b-a22b) is spread over them.
#SBATCH --partition=dc-gpu-devel
#SBATCH --nodes=1
#SBATCH --exclusive
#SBATCH --gres=gpu:4
#SBATCH --time=00:30:00
#SBATCH --job-name=ragstofan-llm

D=${RAG_DIR:?set by scripts/jureca.sh}
MODEL=${RAG_MODEL:-qwen3.8:27b}
export OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=$D/models OLLAMA_CONTEXT_LENGTH=16384 OLLAMA_KEEP_ALIVE=-1

"$D/ollama/bin/ollama" serve &
for i in $(seq 1 60); do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 2; done
# Load the model now, so the first question does not wait for it
curl -sf http://127.0.0.1:11434/api/generate -d "{\"model\":\"$MODEL\",\"keep_alive\":-1}" >/dev/null

umask 077
TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("",0)); print(s.getsockname()[1])')
echo "$TOKEN" > "$D/token.$SLURM_JOB_ID"
trap 'rm -f "$D/token.$SLURM_JOB_ID"' EXIT

GATE_TOKEN=$TOKEN GATE_PORT=$PORT python3 - <<'PY' &
import asyncio, hmac, os

TOKEN = os.environ["GATE_TOKEN"].encode()
PORT = int(os.environ["GATE_PORT"])


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(reader, writer):
    """The first request's headers must carry the token; then the connection is passed to Ollama as it is."""
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return
    given = b""
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"authorization" and value.strip().lower().startswith(b"bearer "):
            given = value.strip()[7:].strip()
    if not hmac.compare_digest(given, TOKEN):
        writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    up_reader, up_writer = await asyncio.open_connection("127.0.0.1", 11434)
    up_writer.write(head)
    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    async with server:
        await server.serve_forever()

asyncio.run(main())
PY

echo "READY $(hostname) $PORT $MODEL"
wait

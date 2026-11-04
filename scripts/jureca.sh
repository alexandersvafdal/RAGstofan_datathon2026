#!/bin/bash
# RAGstofan on this computer, with the language model on a JURECA GPU node for 30 minutes.
#
#   scripts/jureca.sh            start: asks for the JSC code if needed, waits for the GPU, then runs the server
#   Ctrl+C                       stop: ends the GPU job early (saves the course's GPU hours) and closes the tunnel
#
# The phone app reaches this computer over Tailscale: in its settings, http://<this computer's Tailscale name>:8000.
# Without Tailscale running, the server listens on this computer only (http://127.0.0.1:8000).
#
# Settings: PARTITION=dc-gpu (the main GPU nodes, often a queue), RAG_MODEL=qwen3.8:27b, PORT=8000.
# Needs the "Host jureca" entry in ~/.ssh/config (shared connection, so the JSC code is asked once per 8 hours).
set -euo pipefail
cd "$(dirname "$0")/.."

HOST=jureca
PARTITION=${PARTITION:-dc-gpu-devel}   # test nodes: usually free, at most 2 hours and 2 jobs at a time
MODEL=${RAG_MODEL:-qwen3.8:27b}
# The models in the app's picker, tested on the 60 organizer questions on an A100 (2 Nov 2026): qwen3.8:27b 52/60 at
# 10 s a question, qwen3.6:35b-a3b 46/60 at 6 s, gpt-oss:120b 39/60 at 15 s. All three fit on the node's 4 GPUs at
# once, so switching does not reload anything. (qwen3:235b-a22b is a thinking-only model: it answered none.)
# The model server: ollama (the default, tested) or vllm (experimental, not yet tested end to end: the 27B at full
# precision on all 4 GPUs, one model). Ollama runs the picker's three models side by side.
ENGINE=${RAG_ENGINE:-ollama}
PICKER=${RAG_MODELS:-qwen3.8:27b,qwen3.6:35b-a3b,gpt-oss:120b}
[ "$ENGINE" = vllm ] && PICKER=$MODEL
PORT=${PORT:-8000}
LOCAL_LLM=$(python3 -c "import socket; s=socket.socket(); s.bind((\"127.0.0.1\", 0)); print(s.getsockname()[1])")   # a free port each run
# The JURECA budget account (project) to charge, from the environment or the untracked .env: JURECA_ACCOUNT=...
ACCOUNT=${JURECA_ACCOUNT:-$(grep -s '^JURECA_ACCOUNT=' .env | cut -d= -f2-)}
[ -n "$ACCOUNT" ] || { echo "Set JURECA_ACCOUNT (your JURECA project) in .env or the environment."; exit 1; }

# 1. The shared connection to JURECA: asks for the SSH key's passphrase and the JSC code once, then stays open
if ! ssh -O check "$HOST" 2>/dev/null; then
  echo "Logging in to JURECA. Enter the code from your authenticator app."
  ssh -fN "$HOST"
fi

# 2. The GPU job. Ollama and the models live in the project folder, under your JURECA user name
REMOTE_DIR=$(ssh "$HOST" "echo /p/project1/$ACCOUNT/\$USER")
JOB=$(ssh "$HOST" "mkdir -p $REMOTE_DIR/logs && RAG_MODEL=$MODEL RAG_ENGINE=$ENGINE RAG_PRELOAD=$PICKER RAG_DIR=$REMOTE_DIR sbatch --parsable --account=$ACCOUNT \
  --partition=$PARTITION --output=$REMOTE_DIR/logs/%x-%j.log --export=ALL" < jureca/ollama.sbatch)
JOB=${JOB%%;*}
LOG=$REMOTE_DIR/logs/ragstofan-llm-$JOB.log
echo "GPU job $JOB submitted to $PARTITION."

cleanup() {
  echo
  echo "Stopping: ending GPU job $JOB and closing the tunnel."
  [ -n "${SERVER:-}" ] && kill "$SERVER" 2>/dev/null   # a background server does not get Ctrl+C itself
  ssh "$HOST" "scancel $JOB" 2>/dev/null || true
  ssh -O cancel -L "127.0.0.1:$LOCAL_LLM:${NODE:-x}i:${GATE:-0}" "$HOST" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# 3. Wait until the model is loaded on the node
while true; do
  STATE=$(ssh "$HOST" "squeue -h -j $JOB -o %T" 2>/dev/null || true)
  READY=$(ssh "$HOST" "grep -h '^READY ' $LOG 2>/dev/null | tail -1" || true)
  [ -n "$READY" ] && break
  if [ -z "$STATE" ]; then echo "The job ended before the model was ready. Its log: $LOG"; exit 1; fi
  echo "  $(date +%H:%M:%S) job is $(echo "$STATE" | tr "[:upper:]" "[:lower:]")..."
  sleep 10
done
read -r _ NODE GATE _ <<< "$READY"
TOKEN=$(ssh "$HOST" "cat $REMOTE_DIR/token.$JOB")
LEFT=$(ssh "$HOST" "squeue -h -j $JOB -o %L")   # time left, e.g. 29:41

# 4. The tunnel: this computer's port $LOCAL_LLM to the gate on the node, through the login node ("jrc0416i" is the
#    node's name on the internal network; its plain name is not reachable from the login node)
ssh -O forward -L "127.0.0.1:$LOCAL_LLM:${NODE}i:$GATE" "$HOST"
curl -sf -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$LOCAL_LLM/v1/models" >/dev/null \
  || { echo "The model on $NODE does not answer through the tunnel."; exit 1; }

# 5. The server: on the Tailscale address when Tailscale runs (the phone), else on this computer only
TS=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
BIND=127.0.0.1
if "$TS" status >/dev/null 2>&1; then
  IP=$("$TS" ip -4 2>/dev/null | head -1 || true)
  # Only an address this computer holds right now (Tailscale can report one while it is stopped)
  [ -n "$IP" ] && python3 -c "import socket,sys; socket.socket().bind((sys.argv[1], 0))" "$IP" 2>/dev/null && BIND=$IP
fi
[ "$BIND" = 127.0.0.1 ] && echo "Tailscale is not running: the server is on this computer only, the phone cannot reach it."
echo
echo "Model $MODEL is ready on $NODE for $LEFT more (minutes:seconds)."
echo "RAGstofan: http://$BIND:$PORT   (Ctrl+C stops the server and the GPU job)"
echo

echo "Models in the picker: $PICKER"
LLM_BASE_URL="http://127.0.0.1:$LOCAL_LLM/v1" LLM_API_KEY="$TOKEN" LLM_MODEL="$MODEL" FAST_MODEL="$MODEL" \
MODELS="$PICKER" LLM_CONTEXT=16384 LLM_TEMPLATE_KWARGS=$([ "$ENGINE" = vllm ] && echo 1 || echo 0) \
  .venv/bin/uvicorn hagbot.api:app --host "$BIND" --port "$PORT" &
SERVER=$!

# When the GPU job ends (its time is up), the server goes too: without the model every question would fail
while kill -0 "$SERVER" 2>/dev/null; do
  sleep 30
  if [ -z "$(ssh "$HOST" "squeue -h -j $JOB -o %T" 2>/dev/null)" ]; then
    echo
    echo "The GPU time is up. Run scripts/jureca.sh again for another 30 minutes."
    kill "$SERVER" 2>/dev/null
  fi
done
wait "$SERVER" 2>/dev/null || true

#!/bin/bash
# Start RAGstofan on the Spark: Tailscale, the server (port 8000) and voice (port 8001), each in its own tmux session.
# Runs at boot from the hackathon user's crontab:  @reboot $HOME/RAGstofan/scripts/start_spark.sh
# Safe to run by hand too: a session that is already running is left alone. Ollama starts on its own (system service).
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
APP="$HOME/RAGstofan"
TS="$HOME/tailscale"   # Tailscale's static build, run in user space (no root); keeps its login in $TS/state

start() { tmux has-session -t "$1" 2>/dev/null && echo "$1 already running" || { tmux new-session -d -s "$1" "$2" && echo "$1 started"; }; }

echo "== $(date '+%F %T')"
if [ -x "$TS/tailscaled" ]; then
  start tailscale "$TS/tailscaled --tun=userspace-networking --socket=$TS/tailscaled.sock --statedir=$TS/state 2>&1 | tee $TS/tailscaled.log"
fi
start ragstofan "cd $APP && . .venv/bin/activate && uvicorn hagbot.api:app --host 0.0.0.0 --port 8000 2>&1 | tee $APP/server.log"
if [ -d "$APP/.venv-voice" ]; then
  start voice "cd $APP && HF_HOME=$APP/.voice $APP/.venv-voice/bin/uvicorn voice.server:app --host 0.0.0.0 --port 8001 2>&1 | tee $HOME/voice.log"
fi

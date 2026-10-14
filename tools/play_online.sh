#!/usr/bin/env bash
# Levanta el servidor y lo expone con un túnel público para jugar con amigos.
# Usa cloudflared si está instalado; si no, localhost.run vía ssh (sin instalar nada).
# Uso: tools/play_online.sh [puerto]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8000}"

python3 -m uvicorn server.app:app --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null' EXIT
sleep 2

echo
echo ">> Servidor local: http://localhost:$PORT"
echo ">> Abriendo túnel... comparte con tu amigo la URL https que aparece abajo."
echo ">> (Ctrl+C para terminar)"
echo

if command -v cloudflared >/dev/null; then
  cloudflared tunnel --url "http://localhost:$PORT"
else
  ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 \
      -R "80:localhost:$PORT" nokey@localhost.run
fi

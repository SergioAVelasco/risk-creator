#!/usr/bin/env bash
# Levanta el servidor y lo expone con un túnel público para jugar con amigos.
# Usa cloudflared si está instalado; si no, localhost.run vía ssh (sin instalar nada).
# Uso: tools/play_online.sh [puerto]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8000}"

# Si el puerto ya está ocupado (p. ej. un servidor anterior), el túnel apuntaría
# a ese proceso viejo en vez de al nuevo: mejor abortar con un mensaje claro.
if python3 -c "import socket,sys; sys.exit(socket.socket().connect_ex(('127.0.0.1', $PORT)) != 0)"; then
  echo "El puerto $PORT ya está en uso. Cierra el otro servidor o usa otro puerto:" >&2
  echo "  tools/play_online.sh 8001" >&2
  exit 1
fi

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

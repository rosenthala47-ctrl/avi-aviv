#!/usr/bin/env bash
# Start the API and the Streamlit front end in one container.
#
# Why one container: a PaaS web service exposes exactly one port. Streamlit
# takes it ($PORT) because it is what a human opens; the API stays on an
# internal port that only the front end talks to. Splitting them into two
# deployed services is the right call at real scale — this keeps a demo to
# one free-tier service and one URL.
set -euo pipefail

PORT="${PORT:-8501}"
API_PORT="${CRR_API_PORT:-8000}"
API_WORKERS="${CRR_API_WORKERS:-1}"
export CRR_API_URL="${CRR_API_URL:-http://127.0.0.1:${API_PORT}}"

echo "[start] API on 127.0.0.1:${API_PORT} (${API_WORKERS} worker(s)); UI on 0.0.0.0:${PORT}"

python -m uvicorn crr.api.app:create_app \
  --factory --host 127.0.0.1 --port "${API_PORT}" --workers "${API_WORKERS}" &
API_PID=$!

# Stop the API when this script is asked to stop, so the platform's SIGTERM
# does not leave the worker orphaned during a redeploy.
cleanup() {
  echo "[start] shutting down API (pid ${API_PID})"
  kill "${API_PID}" 2>/dev/null || true
  wait "${API_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Loading two LightGBM models plus their SHAP explainers takes a few seconds,
# and the UI is unusable until that finishes — so block on real readiness
# rather than a fixed sleep that is either wasteful or too short.
echo "[start] waiting for the API to become healthy…"
for attempt in $(seq 1 90); do
  if curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    echo "[start] API healthy after ${attempt}s"
    break
  fi
  if ! kill -0 "${API_PID}" 2>/dev/null; then
    echo "[start] FATAL: the API process exited before becoming healthy" >&2
    exit 1
  fi
  sleep 1
done

if ! curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
  echo "[start] FATAL: API did not become healthy within 90s" >&2
  exit 1
fi

exec python -m streamlit run app.py \
  --server.port "${PORT}" \
  --server.address 0.0.0.0 \
  --server.headless true

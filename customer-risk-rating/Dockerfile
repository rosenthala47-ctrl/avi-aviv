# Customer Risk Rating — API + Streamlit front end in one image.
#
# The models are PRE-TRAINED AND COMMITTED, not trained during the build.
# models/default_12m/ and models/financial_crime_12m/ are real, tracked files
# in this repo (~650 KB total — see .gitignore) rather than generated here.
#
# This used to run generate_synthetic_data.py + train_baseline.py as a build
# step, which is what a self-contained, reproducible-from-a-seed image sounds
# like it should do — but LightGBM training on enough rows to calibrate
# financial_crime_12m's ~1.5% prevalence needs several hundred MB of peak RSS
# (measured directly, see git history on this file for the numbers), and a
# free-tier PaaS build container (Render Free: 512 MB) either OOMs or times
# out trying to do that inside `docker build`. No row count both fits that
# budget and clears the calibration bar, so the honest fix is to stop asking
# the build to train at all: train offline where memory isn't the constraint
# (`make data && make train`, or CI), commit the resulting models/ directory,
# and let the build be what it now is below — copy files, install deps, done.
# That also means build time no longer depends on dataset size or CPU
# contention on a shared free-tier builder.
#
# To ship a freshly retrained model: run `make data train` locally (or in any
# environment without a tight memory ceiling), confirm PHASE 2 EXIT CRITERIA
# prints PASS for both targets (train_baseline.py exits non-zero if not —
# see its --allow-degraded-build for the one legitimate reason to override
# that), then commit the updated models/ directory.
FROM python:3.11-slim

# libgomp1 is LightGBM's OpenMP runtime; curl is used by the healthcheck and
# by start_all.sh to wait for real API readiness.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a source-only change does not reinstall the stack.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY src/ ./src/
# --no-deps: the runtime stack came from requirements.txt above, and resolving
# pyproject's own dependency set again would only risk pulling different pins.
RUN pip install --no-cache-dir --no-deps -e .

COPY config/ ./config/
COPY scripts/ ./scripts/
COPY app.py ./
COPY .streamlit/ ./.streamlit/

# The pre-trained artefacts (see the top-of-file note). Trained on 50,000
# rows — the measured floor for financial_crime_12m's ~1.5% prevalence to
# calibrate — so both targets clear all four phase-2 criteria with headroom
# (9/10 calibration bins each; models/*/metadata.json has the full numbers).
COPY models/ ./models/

# Fail the build, not the first request, if a commit ever ships a models/
# directory ModelBundle can't actually load (missing target, corrupt file).
RUN python -c "from crr.api.scoring import ModelBundle; ModelBundle.load()"

RUN chmod +x scripts/start_all.sh

# Streamlit's port. PaaS platforms override this with their own $PORT.
ENV PORT=8501
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -sf "http://127.0.0.1:${CRR_API_PORT:-8000}/health" || exit 1

CMD ["./scripts/start_all.sh"]

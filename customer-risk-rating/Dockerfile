# Customer Risk Rating — API + Streamlit front end in one image.
#
# The models are TRAINED AT BUILD TIME rather than committed to the repo.
# data/ and models/ are git-ignored on purpose (the dataset is reproducible
# from a seed, and binary model artefacts do not belong in version control),
# so a fresh clone has no model to serve — and `ModelBundle.load` fails loudly
# rather than silently serving nothing. Generating and training during the
# build keeps the image self-contained and reproducible from the seed, and
# costs well under a minute at the default size.
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

# Dataset size for the build-time training run.
#
# Production deployments: use 50k (measured floor). financial_crime_12m has
# ~1.5% prevalence; at 50k both models clear all four criteria with headroom
# (9/10 bins each). At 15k the model fails: 7/10 bins vs 8/10 required.
#
# Low-resource deployments (Render Free tier, 512MB RAM): use 20k. This is
# a trade-off: fits in memory but carries higher calibration risk. If the
# build fails calibration, either upgrade to a higher-tier plan or use
# Railway/similar with better free specs.
ARG CRR_BOOTSTRAP_ROWS=20000

# `set -e` is deliberate: train_baseline.py exits non-zero when a model misses
# phase 2's exit criteria, and an image that silently shipped a miscalibrated
# model would be worse than one that failed to build.
RUN set -eux; \
    python scripts/generate_synthetic_data.py -n "${CRR_BOOTSTRAP_ROWS}" --language mixed; \
    python scripts/train_baseline.py --target default_12m; \
    python scripts/train_baseline.py --target financial_crime_12m; \
    # Serving needs models/ only. The generated dataset is ~130 MB at this size
    # and nothing at runtime reads it, so drop it rather than ship it — it is
    # reproducible from the seed in models/*/metadata.json with one command.
    rm -rf data

RUN chmod +x scripts/start_all.sh

# Streamlit's port. PaaS platforms override this with their own $PORT.
ENV PORT=8501
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -sf "http://127.0.0.1:${CRR_API_PORT:-8000}/health" || exit 1

CMD ["./scripts/start_all.sh"]

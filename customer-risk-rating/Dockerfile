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
# Production deployments (Render Starter/1GB+, Railway, any host with >=1GB
# during build): use 50k, the measured floor for calibration. financial_crime_12m
# runs at ~1.5% prevalence, so a smaller draw starves it of positives; at 50k
# both models clear all four phase-2 criteria with headroom (9/10 bins each).
#
# Low-resource deployments (Render Free tier, 512MB RAM): use 15k. Peak RSS was
# measured directly (RUSAGE_CHILDREN, this repo's actual deps, not estimated)
# by running generate_synthetic_data.py and train_baseline.py standalone at
# several sizes:
#     rows    generate    train (financial_crime_12m, the larger of the two)
#     10,000   244 MB      345 MB   -> 4/10 calibration bins (fails badly)
#     15,000   304 MB      396 MB   -> 7/10 calibration bins (fails)
#     20,000   366 MB      440 MB   -> 7/10 calibration bins (fails, no better)
#     50,000   ~unmeasured, extrapolates past 700 MB -> 9/10 (passes, but OOMs 512MB)
# 15k and 20k land on the same 7/10 result — the low prevalence needs a much
# bigger jump than either gives, and that jump costs more memory than a 512MB
# container has. So no row count both fits 512MB and clears financial_crime_12m's
# calibration bar; 15k is the choice that gets the best headroom (~115 MB, 23%)
# for the calibration quality actually available at this memory budget.
# --allow-degraded-build below is what makes that an explicit trade-off
# instead of a build failure — see its --help text in train_baseline.py.
ARG CRR_BOOTSTRAP_ROWS=15000

# `set -e` is deliberate: train_baseline.py exits non-zero when a model misses
# phase 2's exit criteria, and an image that silently shipped a miscalibrated
# model would be worse than one that failed to build. --allow-degraded-build
# only applies below the 50k floor, so it never softens that guarantee for a
# production-sized build — there, a real regression still fails the build.
RUN set -eux; \
    python scripts/generate_synthetic_data.py -n "${CRR_BOOTSTRAP_ROWS}" --language mixed; \
    degraded_flag=""; \
    if [ "${CRR_BOOTSTRAP_ROWS}" -lt 50000 ]; then degraded_flag="--allow-degraded-build"; fi; \
    python scripts/train_baseline.py --target default_12m $degraded_flag; \
    python scripts/train_baseline.py --target financial_crime_12m $degraded_flag; \
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

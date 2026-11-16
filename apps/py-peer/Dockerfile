FROM python:3.12-slim AS deps
WORKDIR /app
# fastecdsa (a libp2p transitive dependency) has no wheel for every platform and builds from source.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libgmp-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN pip install --no-cache-dir --no-compile -r requirements.lock

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY peer ./peer
RUN useradd --uid 10001 --no-create-home app
USER app
EXPOSE 4001 9100
ENTRYPOINT ["python", "-m", "peer.main"]

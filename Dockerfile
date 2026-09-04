# syntax=docker/dockerfile:1
#
# cortex im Container: isoliert vom System, aber mit vollem Netzzugang.
#
#   docker build -t cortex .                     # mit Browser-Fallback (Default)
#   docker build -t cortex --target slim .       # ohne Browser, ~700 MB kleiner
#
# Der Container schraenkt das Netz bewusst NICHT ein -- cortex muss frei
# suchen und Seiten lesen koennen. Isoliert wird das Dateisystem: der
# Container sieht nur /data (Cache und Verlauf) und /work (Exporte).

# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CORTEX_DATA_DIR=/data \
    PLAYWRIGHT_BROWSERS_PATH=/browsers \
    TERM=xterm-256color \
    HOME=/tmp

# tini raeumt Zombie-Prozesse auf und leitet Strg+C sauber weiter.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

# /data und /work sind fuer jede UID beschreibbar. Das erlaubt es,
# den Container unter der UID des Host-Nutzers laufen zu lassen -- sonst
# gehoerten die Exporte unter Linux dem falschen Benutzer.
RUN useradd --create-home --uid 1000 cortex \
    && mkdir -p /data /work /browsers \
    && chown -R cortex:cortex /data /work /browsers \
    && chmod 0777 /data /work

WORKDIR /app
COPY pyproject.toml README.md ./
COPY cortex ./cortex
RUN pip install --no-cache-dir .

# ---------------------------------------------------------------------------
# Ohne Browser -- Cookie-Stufen 1 und 2 reichen fuer die allermeisten Seiten.
FROM base AS slim

USER cortex
WORKDIR /work
ENTRYPOINT ["/usr/bin/tini", "--", "cortex"]
CMD []

# ---------------------------------------------------------------------------
# Mit Chromium fuer den JavaScript-Fallback (Stufe 3).
FROM base AS browser

RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /browsers \
    && rm -rf /var/lib/apt/lists/* /root/.cache

# Im Container uebernimmt der Container die Isolation, deshalb laeuft
# Chromium hier ohne seine eigene Sandbox (die im Container ohnehin
# zusaetzliche Rechte braeuchte). Ausserhalb bleibt sie aktiv.
ENV CORTEX_BROWSER_NO_SANDBOX=1 \
    CORTEX_ENABLE_PLAYWRIGHT=true

USER cortex
WORKDIR /work
ENTRYPOINT ["/usr/bin/tini", "--", "cortex"]
CMD []

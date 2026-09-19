FROM node:24-alpine AS web-builder

WORKDIR /build
COPY webui/package.json webui/package-lock.json ./webui/
RUN cd webui && npm ci
COPY webui ./webui
COPY tradingagents/web_ui ./tradingagents/web_ui
RUN cd webui && npm run build

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY . .
COPY --from=web-builder /build/tradingagents/web_ui/dist ./tradingagents/web_ui/dist
RUN pip install --no-cache-dir .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents
USER appuser
WORKDIR /home/appuser/app

COPY --from=builder --chown=appuser:appuser /build .

ENTRYPOINT ["tradingagents"]

# Small, non-root image for the rent receipt bot.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# A fixed user ID, so the data folder on the server can be owned by it.
ARG APP_UID=10001
RUN useradd --system --uid "${APP_UID}" --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

USER app
ENTRYPOINT ["python", "-m", "rent_receipt"]
CMD ["--config", "/etc/rent-receipt-bot/config.toml"]

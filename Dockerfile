# Firebase Hosting serves the frontend. This image runs either Cloud Run role.
FROM python:3.11-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    DEMO_MODE=false \
    SERVICE_ROLE=api
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && groupadd --gid 10001 radar \
    && useradd --uid 10001 --gid radar --no-create-home radar \
    && mkdir /app/data \
    && chown radar:radar /app/data
COPY --chown=radar:radar backend ./backend
USER radar
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port \"${PORT:-8080}\" --workers 1 --proxy-headers"]

# Mock services + tooling image (stdlib-only Python). One image, several commands:
#   python -m mocks.telegram | python -m mocks.gemini | python /scripts/<tool>.py
FROM python:3.12.16-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY src/ /app/src/

USER nobody
CMD ["python", "-m", "mocks.telegram"]

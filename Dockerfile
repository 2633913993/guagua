FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY api/media_parser_fastapi/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/media_parser_fastapi/app ./app
COPY api/media_parser_fastapi/run.py .
COPY api/media_parser_fastapi/README.md .

EXPOSE 8080
CMD ["python", "run.py", "--production"]

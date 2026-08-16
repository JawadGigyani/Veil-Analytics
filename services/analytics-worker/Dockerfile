FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY app ./app
RUN pip install --no-cache-dir .
RUN addgroup --system veil && adduser --system --ingroup veil veil && mkdir -p /data && chown veil:veil /data
USER veil
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src/ ./src/
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 1000 demo \
    && mkdir /data && chown demo:demo /data
USER demo
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "security_kql.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

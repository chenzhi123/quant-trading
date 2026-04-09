FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null; \
    pip install --no-cache-dir \
    pandas numpy scipy pydantic pyyaml fastapi "uvicorn[standard]" \
    jinja2 apscheduler loguru matplotlib pytest httpx python-multipart

COPY . .

RUN mkdir -p logs data

ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

CMD ["python", "main.py", "web"]

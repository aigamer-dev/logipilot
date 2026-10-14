FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 PYTHON=python PORT=8000
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml
COPY logipilot ./logipilot
COPY skills ./skills
COPY web ./web
COPY scripts ./scripts
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
EXPOSE 8000
CMD ["scripts/run_all.sh"]

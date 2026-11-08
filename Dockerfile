# Cloud Run container: the app reads PORT and GOOGLE_CLOUD_PROJECT from the environment; Vertex AI
# calls authenticate with the service account's default credentials (no key files needed).
FROM python:3.12-slim

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

COPY . .

# 300s+: deep research fans out to parallel researchers; graders wait up to 5 minutes per answer.
# Cloud Run's own request timeout must be raised too (see README "Deployment").
ENV PORT=8080
CMD ["uv", "run", "--no-dev", "app.py", "--host", "0.0.0.0"]

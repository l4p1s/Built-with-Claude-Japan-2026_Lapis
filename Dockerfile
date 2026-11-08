FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /bin/uv

# ソースはホストからマウントするので、root 所有のキャッシュをホスト側に作らない。
# 仮想環境は /app の外に置く（マウントしても消えないように）
ENV PYTHONDONTWRITEBYTECODE=1 \
    RUFF_NO_CACHE=true \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY . .

# アクセスログは出さない。GET /v1/questions?lat=&lon= などで利用者の位置が URL に入り、ログに残ると削除できないため
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

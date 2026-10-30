# Returns Desk board. Built in the cloud with `az acr build`, so no local Docker needed.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first so they cache separately from the source.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# Run as an unprivileged user; it owns /app so the resolution log under logs/ stays writable.
RUN useradd --system --uid 10001 --no-create-home app && chown -R app /app
USER 10001

ENV PATH="/app/.venv/bin:$PATH" PORT=8080
EXPOSE 8080
# App logs go to stderr, which Container Apps sends to Log Analytics. The resolution log stays at
# its default path inside the container and is emptied with the board's demo reset.
CMD ["returns-desk", "serve", "--host", "0.0.0.0"]

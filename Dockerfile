FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir .
USER 65532:65532
ENTRYPOINT ["glean-ai"]
CMD ["--help"]

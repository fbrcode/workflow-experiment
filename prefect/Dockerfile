# Image for the Prefect worker and the deploy step: the official Prefect image plus this package.
# Dependencies are installed from uv.lock, so the image runs what the tests ran against.
FROM prefecthq/prefect:3.9.3-python3.12@sha256:1f32e854effefa290527f87a0a10346f6f84ce31047d0e3e5a4d4210d6450a30

WORKDIR /opt/prefect/email-summarizer
# Every flow run is a new Python process: compile bytecode once at build time, not at each import.
ENV UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --output-file /tmp/requirements.txt \
    && uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt

COPY src ./src
RUN uv pip install --system --no-cache --no-deps .

COPY prefect.yaml probes.yaml deploy.sh ./

FROM python:3.12-slim

# ffmpeg is required at runtime; git is not needed in the image
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies before copying source so layer is cached on dep changes
COPY pyproject.toml README.md ./
# Stub the package so pip can resolve extras without the full source tree
RUN mkdir -p quotegif && touch quotegif/__init__.py

RUN pip install --no-cache-dir -e ".[openai,anthropic,ollama,whisper]"

# Now copy the real source (invalidates only this layer on code changes)
COPY quotegif/ quotegif/

# Standard mount points — keep these consistent with docker-compose.yml
RUN mkdir -p /media /output

# Whisper model cache lives in a named volume mounted at runtime
ENV QUOTEGIF_MEDIA_FOLDERS=/media \
    QUOTEGIF_OUTPUT_DIR=/output

ENTRYPOINT ["python", "-m", "quotegif"]
CMD ["--help"]

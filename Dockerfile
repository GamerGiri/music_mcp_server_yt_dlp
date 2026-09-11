FROM python:3.11-slim

# Install system dependencies:
# - ffmpeg: audio encoding to mono 24kHz Opus
# - curl: web search & downloading Deno
# - ca-certificates: SSL validation
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Deno (official JS challenge solver for yt-dlp YouTube extraction)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="$DENO_INSTALL/bin:$PATH"

WORKDIR /app

# Install latest dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create music storage directory
RUN mkdir -p /app/music

EXPOSE 10000

ENV PORT=10000
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]

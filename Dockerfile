FROM python:3.11-slim

# Install system dependencies:
# - ffmpeg: audio encoding to mono 24kHz Opus
# - curl: DuckDuckGo web search
# - nodejs: JavaScript challenge solver for yt-dlp (bypasses YouTube bot/signature checks)
# - ca-certificates: SSL validation
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl ca-certificates nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install latest dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files (including cookies.txt if present)
COPY . .

# Create music storage directory
RUN mkdir -p /app/music

EXPOSE 10000

ENV PORT=10000
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]

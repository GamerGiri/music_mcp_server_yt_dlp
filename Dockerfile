FROM python:3.11-slim

# Install system dependencies (ffmpeg for opus conversion, curl for web search)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY main.py .

# Create music directory
RUN mkdir -p /app/music

# Expose Render HTTP port
EXPOSE 10000

ENV PORT=10000
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]

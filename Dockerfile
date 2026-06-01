FROM python:3.12-slim-trixie

WORKDIR /app

# System deps for asyncpg, pipecat, azure-cognitiveservices-speech, PyAV (ffmpeg 7).
# trixie ships ffmpeg 7.x natively, which PyAV >=14 requires.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    curl \
    pkg-config \
    ffmpeg \
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libswscale-dev \
    libswresample-dev \
    libavfilter-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip so it picks up newer wheel metadata (PyAV, numba, etc.)
RUN pip install --no-cache-dir --upgrade pip

# Install Python deps before copying source — maximises layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Pipecat voice pipeline exposes WebSocket on 8001 in addition to API on 8000
EXPOSE 8000 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

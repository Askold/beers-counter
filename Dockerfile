FROM python:3.12-slim

WORKDIR /app

# ffmpeg/ffprobe power the nightly circle-video montage (montage.py)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY assets ./assets

CMD ["python", "bot.py"]

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py database.py ./

# SQLite database lives in a volume-mounted directory
VOLUME ["/app/data"]

CMD ["python", "bot.py"]

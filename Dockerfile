FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["gunicorn", "--workers", "1", "--threads", "80", "--timeout", "0", "-b", "0.0.0.0:8080", "main:app"]
FROM python:3.11-slim
WORKDIR /app
ENV IS_CLOUD_RUN=true
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "--workers", "1", "--threads", "80", "--timeout", "0", "-b", "0.0.0.0:8080", "main:app"]
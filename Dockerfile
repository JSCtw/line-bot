# === Cloud Run 標準 Dockerfile ===
FROM python:3.11-slim
WORKDIR /app

# 設定 PYTHONPATH，Gunicorn 才能找到你的 core 和 utils 模組
ENV PYTHONPATH /app

# 僅複製 requirements.txt 並先安裝
# 這樣可以利用 GCP 的建置快取，未來若只改程式碼，不用重裝套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有程式碼
COPY . .

# 
# 關鍵：
# Cloud Run 會透過 $PORT 環境變數告訴你的容器要監聽哪個埠號。
# 我們必須使用 $PORT，而不是寫死的 8080。
#
CMD ["gunicorn", "--workers", "1", "--threads", "80", "--timeout", "0", "-b", "0.0.0.0:$PORT", "main:app"]
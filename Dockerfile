# === Cloud Run 標準 Dockerfile (v4.2 - RUN 快取清除) ===
FROM python:3.11-slim
WORKDIR /app

# 設定 PYTHONPATH，Gunicorn 才能找到你的 core 和 utils 模組
ENV PYTHONPATH /app

# 僅複製 requirements.txt 並先安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有程式碼
COPY . .

# 
# 關鍵修復：
# 移除 [ 和 ]，將 CMD 改為 "shell form" (純字串)
# 這樣 /bin/sh 才會介入，並將 $PORT 變數正確替換為 8080
#
CMD gunicorn --workers 1 --threads 80 --timeout 0 -b 0.0.0.0:$PORT main:app
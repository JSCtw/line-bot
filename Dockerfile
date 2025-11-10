#DOCKERFILES  (對應v3.20)
# === 快取清除 ===
FROM python:3.11-slim
WORKDIR /app

# 我們在 Dockerfile 中設定 PYTHONPATH
ENV PYTHONPATH /app

# [快取清除]
# 使用當前時間戳（或隨機數）寫入一個檔案
# 這會強制 Docker 認為這一層永遠是新的
# 導致下面的 COPY 和 RUN 永遠不會使用快取
# 每次部署時，這一層都會改變，從而使所有後續層失效
ARG CACHE_BUSTER=default
RUN echo "Cache bust version: $(date +%s)" > /cache_buster.txt

# 我們將 pip install 拆開並加入 --no-cache-dir
# 由於 CACHE_BUSTER 層的變更，這一層將被強制重新執行
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 我們在建置時印出版本，用於除錯
RUN pip show line-bot-sdk

# 複製剩餘的程式碼
# 這一層也將被強制重新複製
COPY . .

# 啟動 Gunicorn
CMD ["gunicorn", "--workers", "1", "--threads", "80", "--timeout", "0", "-b", "0.0.0.0:8080", "main:app"]
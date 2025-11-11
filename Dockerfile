#Dockerfile (v3.3 - 適用 Cloud Run)
FROM python:3.11-slim
WORKDIR /app

# 設定 PYTHONPATH，Gunicorn 才能找到你的 core 和 utils 模組
ENV PYTHONPATH /app

# 僅複製 requirements.txt 並先安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有程式碼
COPY . .

# [v3.2修正] 移除 [ 和 ]，將 CMD 改為 "shell form" (純字串)
# [v3.2修正] 這樣 /bin/sh 才會介入，並將 $PORT 變數正確替換為 8080
# [v3.3修正] 將 gunicorn 指令加入 -k (worker class) 參數
CMD gunicorn -k uvicorn.workers.UvicornWorker --workers 1 --threads 80 --timeout 0 -b 0.0.0.0:$PORT main:app
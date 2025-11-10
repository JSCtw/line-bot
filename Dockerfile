#DOCKERFILES  (對應v3.20)
FROM python:3.11-slim

WORKDIR /app

ENV IS_CLOUD_RUN=true

# --- ❗️【修復】---
# 
# 將 /app 這個目錄「永久」加入到 Python 的模組搜尋路徑中
# 這會讓 Python 在執行 import utils 時，能正確找到 /app/utils
#
ENV PYTHONPATH /app
# 
# --- ❗️【修復完畢】---

COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

CMD ["gunicorn", "--workers", "1", "--threads", "80", "--timeout", "0", "-b", "0.0.0.0:8080", "main:app"]
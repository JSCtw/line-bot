# === 4度修復：手動強制重裝 SDK ===
FROM python:3.11-slim
WORKDIR /app

ENV PYTHONPATH /app

# [快取清除]
# 雖然這可能被 Zeabur 忽略，但我們保留它
RUN echo "Cache bust version: $(date +%s)" > /cache_buster.txt

COPY requirements.txt .

# [修復]
# 我們假設 Zeabur 在這裡執行 pip install 時使用了損壞的快取。
# 我們手動、強制地卸載它，然後再從 PyPI 乾淨地安裝一次 v3.21.0。
# 這確保了無論快取是什麼，容器中的最終版本都是乾淨的。
RUN pip install --no-cache-dir -r requirements.txt \
    && echo "=== [DEBUG] Force uninstalling potentially corrupted SDK ===" \
    && pip uninstall -y line-bot-sdk \
    && echo "=== [DEBUG] Force installing clean SDK v3.21.0 from PyPI ===" \
    && pip install --no-cache-dir line-bot-sdk==3.21.0

# 我們在建置時印出版本，用於除錯
RUN echo "=== [DEBUG] Final SDK version installed: ===" \
    && pip show line-bot-sdk

# 複製剩餘的程式碼
COPY . .

# 啟動 Gunicorn
CMD ["gunicorn", "--workers", "1", "--threads", "80", "--timeout", "0", "-b", "0.0.0.0:8080", "main:app"]
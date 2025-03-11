from flask import Flask, request
from linebot.v3.webhook import WebhookParser
from dotenv import load_dotenv
import os

app = Flask(__name__)

# 載入 .env 檔案
load_dotenv()
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# 初始化 Webhook Parser
parser = WebhookParser(channel_secret=LINE_CHANNEL_SECRET)

@app.route("/webhook", methods=["POST"])
def webhook():
    # 獲取請求頭中的簽名
    signature = request.headers.get("X-Line-Signature", "")
    
    # 解析 Webhook 事件
    body = request.get_data(as_text=True)
    try:
        events = parser.parse(body, signature)
        for event in events:
            # 提取 userId
            user_id = event.source.user_id
            print(f"User ID: {user_id}")
            # 可選：將 userId 保存到檔案
            with open("user_ids.txt", "a") as f:
                f.write(f"{user_id}\n")
    except Exception as e:
        print(f"Webhook 解析失敗: {e}")  # 修正為半形冒號
    
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
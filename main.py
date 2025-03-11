from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os

# 初始化 FastAPI 應用
app = FastAPI()

# 設定 LINE Bot 金鑰（手動填入你的 Channel Access Token & Channel Secret）
LINE_ACCESS_TOKEN = "TMNureGniX8GgvHWQLHYppumnWfUGdOfZjU4m0ju+qzgRk8SFmQgts7QGoSnasdeLMQXNB4Zb45cmeuflE2j/nqGjE7RQGWw0dpQnOauw0RXe+gHjfSLH+qNnwVcOX9DN5CviVQq2I0kqO4AZBogGAdB04t89/1O/w1cDnyilFU="
LINE_SECRET = "fc1d0254f5de5520781347f39ade0752"

line_bot_api = LineBotApi(LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_SECRET)

# Webhook API：接收來自 LINE 伺服器的訊息
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    try:
        handler.handle(body.decode(), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid Signature")

    return "OK"

# 處理文字訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_message = event.message.text
    reply_message = f"你說的是：{user_message}"

    # 回覆用戶
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_message))

# 啟動 FastAPI 伺服器（若直接執行此檔案）
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
 
# Windows CMD

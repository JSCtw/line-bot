# main.py
# -*- coding: utf-8 -*-
"""
LINE Bot 新聞推播系統 - v3.0
"""

import asyncio
import os
import sys
import traceback
from typing import Dict, Optional

# ❗️【新增】匯入 requests，用於發送錯誤警報
import requests
from dotenv import load_dotenv
from flask import Flask, request, abort

# ❗️【新增】匯入 LINE Bot SDK Webhook 處理相關模組
from linebot.v3.webhook import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiException,
    TextSendMessage, # ❗️【新增】匯入 TextSendMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# 匯入所有核心組件 (保持不變)
from utils.config_manager import ConfigManager
from utils.http_client import AsyncHTTPClient
from core import (
    NewsFetcher, NewsClassifier, NewsProcessor,
    LineNotifier, SheetManager
)
from utils.logger import get_logger

# ==============================================================================
# 全域設定與初始化
# ==============================================================================

# ❗️【新增】在所有操作前，首先從 .env 檔案載入環境變數
load_dotenv(verbose=True)

# 初始化日誌 (保持不變)
logger = get_logger(__name__)

# 載入設定檔 (保持不變)
config = ConfigManager().load_config()

# --- ❗️【新增】定義觸發詞 ---
TRIGGER_WORDS = ['🆕', 'news', 'News', 'NEWS']
# ---------------------------

# --- ❗️【新增】錯誤警報輔助函式 ---
def send_discord_alert(message: str, error: Exception = None):
    """發送一個簡單的警報到 Discord Webhook (同步)"""
    # 從環境變數讀取 Webhook URL
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set. Skipping alert.")
        return

    # 組合訊息內容
    app_name = config.get('app', {}).get('name', 'LINE Bot')
    content = f"🔴 **{app_name} 錯誤警報** 🔴\n**Message:** {message}\n"
    
    if error:
        # 獲取完整的 traceback
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        # 限制 traceback 長度，避免超過 Discord 限制
        error_details = f"{str(error)}\n\n{tb_str}"
        if len(error_details) > 1500:
            error_details = error_details[:1500] + "\n... (Traceback Truncated) ..."
        
        content += f"**Error:**\n```\n{error_details}\n```"

    try:
        # 截斷總 content 以防萬一 (Discord 限制 2000 chars)
        if len(content) > 1950:
            content = content[:1950] + " ... (Content Truncated) ..."

        payload = {"content": content}
        
        # 使用 requests (同步) 發送。
        # 在 except 區塊中，或非同步迴圈之外呼叫是可接受的。
        requests.post(webhook_url, json=payload, timeout=5)
    except Exception as e:
        # 如果連警報都發不出去，只能記錄日誌
        logger.error(f"CRITICAL: Failed to send Discord alert: {e}")
# ---------------------------


# ==============================================================================
# NewsBot 主類別 (包含 run_pipeline 的修改)
# ==============================================================================
class NewsBot:
    """新聞機器人主控制器 - 統籌所有組件"""

    def __init__(self, config: Dict):
        """初始化 NewsBot 的所有組件。"""
        self.config = config
        self.http_client: Optional[AsyncHTTPClient] = None
        self.sheet_manager: Optional[SheetManager] = None
        self.news_fetcher: Optional[NewsFetcher] = None
        self.news_classifier: Optional[NewsClassifier] = None
        self.news_processor: Optional[NewsProcessor] = None
        self.line_notifier: Optional[LineNotifier] = None
        self._initialize_components()

    def _initialize_components(self):
        """初始化所有系統組件"""
        # (此方法完全保持不變)
        try:
            self.http_client = AsyncHTTPClient(self.config)
            
            # --- ❗️【重要修改】
            # 確保 SheetManager 使用您修改後的版本 (從環境變數讀取)
            # 這裡的 config 應已包含 worksheet_names
            self.sheet_manager = SheetManager(
                self.config.get('google_sheets', {}).get('worksheets', {}),
                self.config.get('google_sheets', {}).get('credentials_path', 'service_account.json')
            )
            # --- 
            
            self.news_fetcher = NewsFetcher(self.config, self.http_client)
            self.news_classifier = NewsClassifier(self.config, self.http_client)
            self.news_processor = NewsProcessor(self.config, self.http_client, self.sheet_manager)
            self.line_notifier = LineNotifier(self.config)
            logger.info("所有系統組件初始化成功")
        except Exception as e:
            logger.error(f"系統組件初始化失敗: {e}", exc_info=True)
            # --- ❗️【新增】啟動時失敗也要警報 ---
            send_discord_alert("系統組件初始化失敗", e)
            raise

    # ❗️【核心修改】run_pipeline 現在接收一個 target_id 參數
    async def run_pipeline(self, target_id: str) -> None:
        """執行完整的新聞處理流水線，並將結果發送到指定的 target_id"""
        logger.info("=" * 50)
        logger.info(f"🚀 啟動新聞處理流水線 (目標: {target_id})")
        logger.info("=" * 50)
        
        try:
            # (步驟 1 到 4 的核心邏輯完全保持不變)
            # ❗️【重要修改】
            # 根據您提供的 SheetManager.py， initialize() / load_glossary() / get_sent_links() 
            # 都是同步函式，不應使用 await。
            # 如果您後來把它們改成了非同步，請加回 await
            logger.info("📊 初始化 Google Sheets 連線...")
            # await self.sheet_manager.initialize() # 假設 initialize 是同步的
            
            glossary = self.sheet_manager.get_glossary() # 移除非同步 await
            logger.info(f"📚 載入術語表: {len(glossary)} 條術語")
            sent_links = self.sheet_manager.get_sent_links() # 移除非同步 await
            logger.info(f"📝 載入已發送記錄: {len(sent_links)} 條")
            
            logger.info("📰 開始抓取新聞來源...")
            all_news = await self.news_fetcher.fetch_all_news()
            
            logger.info("🎯 開始新聞分類與過濾...")
            important_news = await self.news_classifier.classify_and_filter(all_news, sent_links)
            logger.info(f"✨ 過濾出 {len(important_news)} 則重要新聞")
            
            logger.info("📝 開始新聞處理與摘要生成...")
            processed_news = await self.news_processor.process_news(important_news, glossary)
            logger.info(f"🎉 生成 {len(processed_news)} 則處理後新聞")
            
            if not processed_news:
                logger.info("ℹ️ 沒有需要推播的新聞")
                # ❗️【修改】改用 line_notifier 中的方法
                await self.line_notifier.send_push_message_text(target_id, "太棒了！您已掌握所有最新動態，目前沒有更多新聞。")
                return

            # ❗️【核心修改】將結果推播到動態的 target_id
            logger.info("📱 開始發送 LINE 通知...")
            await self.line_notifier.send_news_batch(processed_news, target_id)
            
            logger.info("💾 記錄發送結果到 Google Sheets...")
            self.sheet_manager.log_sent_links([news['link'] for news in processed_news]) # 移除非同步 await
            
            logger.info(f"🎊 新聞流水線執行完成！已發送至 {target_id}")
            
        except Exception as e:
            logger.error(f"💥 流水線執行失敗: {e}", exc_info=True)
            
            # --- ❗️【新增】發送 Discord 警報 ---
            send_discord_alert(f"Pipeline 執行失敗 (Target: {target_id})", e)
            # ------------------------------------
            
            # 嘗試通知觸發者或管理員
            try:
                error_message = f"抱歉，處理新聞時發生錯誤。\n錯誤: {str(e)[:50]}...\n我們已收到通知並將盡快修復。"
                await self.line_notifier.send_push_message_text(target_id, error_message)
            except Exception as notify_e:
                logger.error(f"連錯誤通知都發不出去: {notify_e}")

# ==============================================================================
# Flask 應用程式與 Webhook 端點
# ==============================================================================

# ❗️【新增】建立 Flask App
app = Flask(__name__)

# ❗️【新增】建立 NewsBot 的全域實例，供所有請求使用
try:
    news_bot = NewsBot(config)
except Exception as e:
    logger.critical(f"NewsBot 實例化失敗，無法啟動服務: {e}", exc_info=True)
    # 在啟動時發送最後的警報
    send_discord_alert("NewsBot 實例化失敗，服務無法啟動", e)
    sys.exit(1) # 啟動失敗，退出
    
# ❗️【新增】建立 Webhook 處理器
channel_secret = os.getenv('LINE_CHANNEL_SECRET')
if not channel_secret:
    logger.error("環境變數 LINE_CHANNEL_SECRET 未設定，無法啟動 Web 伺服器")
    sys.exit(1)
handler = WebhookHandler(channel_secret)


# ❗️【新增】入口 A: LINE 使用者觸發
@app.route("/callback", methods=['POST'])
async def callback():
    """處理來自 LINE 的 Webhook 事件"""
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    
    logger.info(f"收到來自 LINE 的 Webhook 請求 - Body: {body}")

    if not body:
        logger.info("收到了空的請求 body，可能是來自 LINE 後台的 Verify 請求，直接回傳 OK。")
        return 'OK'

    try:
        await handler.handle_async(body, signature)
    except InvalidSignatureError:
        logger.error("Webhook 簽名驗證失敗，請檢查您的 Channel Secret。")
        abort(400)
    except ApiException as e:
        logger.error(f"處理 LINE Webhook 時發生 API 錯誤: {e.body}")
        abort(500)
    except Exception as e:
        logger.error(f"處理 Webhook 時發生未知錯誤: {e}", exc_info=True)
        # --- ❗️【新增】發送 Discord 警報 ---
        send_discord_alert("Webhook /callback 發生嚴重錯誤", e)
        # ------------------------------------
        abort(500)
        
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
async def handle_message(event):
    """處理文字訊息事件"""
    
    # --- ❗️【修改】---
    text = event.message.text.strip()
    
    # 檢查訊息是否在觸發詞列表中
    if text in TRIGGER_WORDS:
        # 判斷事件來源是使用者還是群組
        source_id = event.source.user_id if event.source.type == 'user' else event.source.group_id
        if not source_id: # 備用，理論上不會發生
             source_id = event.source.user_id
        
        logger.info(f"收到觸發指令 '{text}'，來自 ID: {source_id}")

        # --- ❗️【修改】將執行邏輯包在 try...except 中 ---
        try:
            # 1. 立即回覆 (免費)，告知使用者已收到指令
            await news_bot.line_notifier.send_reply_message_text(
                event.reply_token,
                "✅ 收到指令！正在為您準備最新國際新聞，過程約需 40-60 秒，請稍候..."
            )

            # 2. 在背景執行耗時的新聞處理流程
            logger.info(f"正在為 {source_id} 啟動背景新聞處理任務...")
            asyncio.create_task(news_bot.run_pipeline(target_id=source_id))
            
        except Exception as e:
            logger.error(f"handle_message 失敗 (Source: {source_id}): {e}", exc_info=True)
            # --- ❗️【新增】發送 Discord 警報 ---
            send_discord_alert(f"handle_message 失敗 (Source: {source_id})", e)
            # --- ❗️【新增】嘗試通知使用者 ---
            try:
                await news_bot.line_notifier.send_push_message_text(
                    source_id,
                    f"抱歉，啟動新聞處理時發生錯誤。\n我們已收到通知並將盡快修復。"
                )
            except Exception as push_e:
                logger.error(f"連錯誤通知都發不出去: {push_e}")
        # --- ❗️【修改完畢】---

    else:
        # --- ❗️【新增】處理非觸發詞 ---
        logger.info(f"忽略非觸發詞 '{text}'，來自 ID: {event.source.user_id}")
        # (可選) 在此回覆幫助訊息
        # await news_bot.line_notifier.send_reply_message_text(
        #     event.reply_token,
        #     f"您好！請輸入 '{TRIGGER_WORDS[0]}' 來開始處理新聞。"
        # )
        

# ❗️【新增】入口 B: n8n 定時觸發
@app.route("/trigger-push", methods=['POST'])
async def trigger_push():
    """處理來自 n8n 的定時推播請求"""
    secret_key = os.getenv('TRIGGER_SECRET_KEY')
    auth_header = request.headers.get('Authorization')
    if not secret_key or auth_header != f"Bearer {secret_key}":
        logger.warning("收到未經授權的 trigger-push 請求")
        abort(401)
        
    default_target_id = os.getenv("USER_ID")
    if not default_target_id:
        logger.error("環境變數 USER_ID 未設定，無法執行定時推播")
        send_discord_alert("trigger-push 失敗：USER_ID 未設定", None)
        return "Error: USER_ID not configured", 500
        
    logger.info(f"收到 n8n 定時觸發指令，將推播至預設 ID: {default_target_id}")
    
    # --- ❗️【修改】新增 try...except ---
    try:
        asyncio.create_task(news_bot.run_pipeline(target_id=default_target_id))
        return "OK: News pipeline triggered for default user.", 200
    except Exception as e:
        logger.error(f"啟動 /trigger-push 任務失敗: {e}", exc_info=True)
        send_discord_alert("啟動 /trigger-push 任務失敗", e)
        return "Error: Failed to create background task", 500
    # --- ❗️【修改完畢】---


# ❗️【新增】健康檢查端點
@app.route("/", methods=["GET"])
def health_check():
    return f"{config['app']['name']} v{config['app']['version']} is running.", 200

# ==============================================================================
# 本地開發伺服器啟動
# ==============================================================================
if __name__ == "__main__":
    # ❗️【核心修改】本地執行時，啟動 Flask 開發伺服器
    # 部署到 Zeabur 時，他們會使用 Gunicorn 等專業伺服器來啟動您的 app 物件
    port = int(os.getenv("PORT", 8080))
    logger.info(f"啟動 Flask 開發伺服器於 http://0.0.0.0:{port}")
    # ❗️ 關閉 debug 模式，因為您已經有很好的日誌
    app.run(host='0.0.0.0', port=port, debug=False)
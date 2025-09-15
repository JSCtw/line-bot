# main.py (⭐️ 雙軌制 Web 伺服器版)
# -*- coding: utf-8 -*-
"""
LINE Bot 新聞推播系統 - v2.1 (Webhook & Cron 雙軌制)
"""

import asyncio
import os
import sys
import traceback
from typing import Dict, Optional

from dotenv import load_dotenv
from flask import Flask, request, abort

# ❗️【新增】匯入 LINE Bot SDK Webhook 處理相關模組
from linebot.v3.webhook import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import ApiException
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
            self.sheet_manager = SheetManager(self.config)
            self.news_fetcher = NewsFetcher(self.config, self.http_client)
            self.news_classifier = NewsClassifier(self.config, self.http_client)
            self.news_processor = NewsProcessor(self.config, self.http_client, self.sheet_manager)
            self.line_notifier = LineNotifier(self.config)
            logger.info("所有系統組件初始化成功")
        except Exception as e:
            logger.error(f"系統組件初始化失敗: {e}", exc_info=True)
            raise

    # ❗️【核心修改】run_pipeline 現在接收一個 target_id 參數
    async def run_pipeline(self, target_id: str) -> None:
        """執行完整的新聞處理流水線，並將結果發送到指定的 target_id"""
        logger.info("=" * 50)
        logger.info(f"🚀 啟動新聞處理流水線 (目標: {target_id})")
        logger.info("=" * 50)
        
        try:
            # (步驟 1 到 4 的核心邏輯完全保持不變)
            logger.info("📊 初始化 Google Sheets 連線...")
            await self.sheet_manager.initialize()
            glossary = await self.sheet_manager.load_glossary()
            logger.info(f"📚 載入術語表: {len(glossary)} 條術語")
            sent_links = await self.sheet_manager.get_sent_links()
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
                await self.line_notifier.send_reply_message_text(target_id, "太棒了！您已掌握所有最新動態，目前沒有更多新聞。")
                return

            # ❗️【核心修改】將結果推播到動態的 target_id
            logger.info("📱 開始發送 LINE 通知...")
            await self.line_notifier.send_news_batch(processed_news, target_id)
            
            logger.info("💾 記錄發送結果到 Google Sheets...")
            await self.sheet_manager.log_sent_news(processed_news)
            
            logger.info(f"🎊 新聞流水線執行完成！已發送至 {target_id}")
            
        except Exception as e:
            logger.error(f"💥 流水線執行失敗: {e}", exc_info=True)
            # 考慮發送一條錯誤通知給管理者
            admin_id = os.getenv("ADMIN_USER_ID")
            if admin_id:
                error_message = f"新聞機器人系統錯誤：\n{str(e)[:100]}..."
                await self.line_notifier.send_push_message_text(admin_id, error_message)

# ==============================================================================
# Flask 應用程式與 Webhook 端點
# ==============================================================================

# ❗️【新增】建立 Flask App
app = Flask(__name__)

# ❗️【新增】建立 NewsBot 的全域實例，供所有請求使用
news_bot = NewsBot(config)

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
    
    # ❗️ 在處理前先記錄 body，方便除錯
    logger.info(f"收到來自 LINE 的 Webhook 請求 - Body: {body}")

    # ❗️ 檢查 body 是否為空，以應對 LINE 的 Verify 請求
    if not body:
        logger.info("收到了空的請求 body，可能是來自 LINE 後台的 Verify 請求，直接回傳 OK。")
        return 'OK'

    try:
        # ❗️ 使用 await 來異步處理 handle
        await handler.handle_async(body, signature)
    except InvalidSignatureError:
        logger.error("Webhook 簽名驗證失敗，請檢查您的 Channel Secret。")
        abort(400)
    except ApiException as e:
        logger.error(f"處理 LINE Webhook 時發生 API 錯誤: {e.body}")
        abort(500)
    except Exception as e:
        logger.error(f"處理 Webhook 時發生未知錯誤: {e}", exc_info=True)
        abort(500)
        
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
async def handle_message(event):
    """處理文字訊息事件"""
    if event.message.text == '🆕':
        # 判斷事件來源是使用者還是群組
        source_id = event.source.user_id if event.source.type == 'user' else event.source.group_id
        logger.info(f"收到「🆕」指令，來自 ID: {source_id}")

        # 1. 立即回覆 (免費)，告知使用者已收到指令
        await news_bot.line_notifier.send_reply_message_text(
            event.reply_token,
            "✅ 收到指令！正在為您準備最新國際新聞，過程約需 40-60 秒，請稍候..."
        )

        # 2. 在背景執行耗時的新聞處理流程
        logger.info(f"正在為 {source_id} 啟動背景新聞處理任務...")
        asyncio.create_task(news_bot.run_pipeline(target_id=source_id))


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
        return "Error: USER_ID not configured", 500
        
    logger.info(f"收到 n8n 定時觸發指令，將推播至預設 ID: {default_target_id}")
    
    asyncio.create_task(news_bot.run_pipeline(target_id=default_target_id))
    
    return "OK: News pipeline triggered for default user.", 200


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
    app.run(host='0.0.0.0', port=port, debug=False)
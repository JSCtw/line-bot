# main.py (v3.6- FastAPI 版本)
# -*- coding: utf-8 -*-
"""
LINE Bot 新聞推播系統 - v3.6
"""

from dotenv import load_dotenv

load_dotenv(verbose=True)  # 本地開發時自動載入 .env

import sys
import os

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import asyncio
import traceback
from typing import Dict, Optional
import atexit
import requests

# [v3.6] 導入 FastAPI 相關模組
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import PlainTextResponse

# [v3.6] 移除了 Flask 的導入
# from flask import Flask, request, abort

# LINE Bot SDK
from linebot.v3.webhook import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import ApiException
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# 核心組件 (保持不變)
from utils.config_manager import ConfigManager
from utils.http_client import AsyncHTTPClient
from core import NewsFetcher, NewsClassifier, NewsProcessor, LineNotifier, SheetManager
from utils.logger import get_logger

# ==============================================================================
# 全域設定與初始化
# ==============================================================================

logger = get_logger(__name__)
config = ConfigManager().load_config()

# 定義觸發詞 (保持不變)
TRIGGER_WORDS = ["🆕", "news", "News", "NEWS"]

# [v3.6] 建立 FastAPI app
app = FastAPI(
    title=config.get("app", {}).get("name", "LINE Bot"),
    version=config.get("app", {}).get("version", "3.6.0"),
)

# ==============================================================================
# 警報函式 (保持不變)
# ==============================================================================


def _build_alert_payload(message: str, error: Exception = None) -> Optional[Dict]:
    """(內部) 建立警報的 payload"""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return None

    app_name = config.get("app", {}).get("name", "LINE Bot")
    content = f"🔴 **{app_name} 錯誤警報** 🔴\n**Message:** {message}\n"

    if error:
        tb_str = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        error_details = f"{str(error)}\n\n{tb_str}"
        if len(error_details) > 1500:
            error_details = error_details[:1500] + "\n... (Traceback Truncated) ..."
        content += f"**Error:**\n```\n{error_details}\n```"

    if len(content) > 1950:
        content = content[:1950] + " ... (Content Truncated) ..."

    return {"webhook_url": webhook_url, "payload": {"content": content}}


def _sync_send_discord_alert(message: str, error: Exception = None):
    """(同步 - 阻塞) 僅用於 Gunicorn 啟動失敗時的警報"""
    alert_data = _build_alert_payload(message, error)
    if not alert_data:
        logger.warning("DISCORD_WEBHOOK_URL (Sync): 未設定。跳過警報。")
        return

    try:
        requests.post(alert_data["webhook_url"], json=alert_data["payload"], timeout=5)
    except Exception as e:
        logger.error(f"CRITICAL: 發送 SYNC Discord 警報失敗: {e}")


async def _async_send_discord_alert(
    http_client: AsyncHTTPClient, message: str, error: Exception = None
):
    """(非同步 - 不阻塞) 用於所有 Webhook 執行期間的警報"""
    alert_data = _build_alert_payload(message, error)
    if not http_client:
        logger.error("Async Discord 警報失敗: HTTP client 為 None。")
        return
    if not alert_data:
        logger.warning("DISCORD_WEBHOOK_URL (Async): 未設定。跳過警報。")
        return

    try:
        await http_client.post_json(
            alert_data["webhook_url"], json_payload=alert_data["payload"]
        )
    except Exception as e:
        logger.error(f"CRITICAL: 發送 ASYNC Discord 警報失敗: {e}")


# ==============================================================================
# NewsBot 主類別 (保持不變)
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

    def _initialize_components(self):
        """初始化所有系統組件"""
        try:
            self.http_client = AsyncHTTPClient(self.config)
            self.sheet_manager = SheetManager(self.config)
            self.news_fetcher = NewsFetcher(self.config, self.http_client)
            self.news_classifier = NewsClassifier(self.config, self.http_client)
            self.news_processor = NewsProcessor(
                self.config, self.http_client, self.sheet_manager
            )
            self.line_notifier = LineNotifier(self.config)
            logger.info("所有系統組件初始化成功")
        except Exception as e:
            logger.error(f"系統組件初始化失敗: {e}", exc_info=True)
            _sync_send_discord_alert("系統組件初始化失敗", e)
            raise

    async def run_pipeline(self, target_id: str) -> None:
        """執行完整的新聞處理流水線 (邏輯保持不變)"""
        logger.info("=" * 50)
        logger.info(f"🚀 啟動新聞處理流水線 (目標: {target_id})")
        logger.info("=" * 50)

        try:
            # (步驟 1)
            logger.info("📊 初始化 Google Sheets 連線...")
            await self.sheet_manager.initialize()
            glossary = await self.sheet_manager.load_glossary()
            logger.info(f"📚 載入術語表: {len(glossary)} 條術語")
            sent_links = await self.sheet_manager.get_sent_links()
            logger.info(f"📝 載入已發送記錄: {len(sent_links)} 條")

            # (步驟 2)
            logger.info("📰 開始抓取新聞來源...")
            all_news = await self.news_fetcher.fetch_all_news()

            # (步驟 3)
            logger.info("🎯 開始新聞分類與過濾...")
            important_news = await self.news_classifier.classify_and_filter(
                all_news, sent_links
            )
            logger.info(f"✨ 過濾出 {len(important_news)} 則重要新聞")

            # (步驟 4)
            logger.info("📝 開始新聞處理與摘要生成...")
            processed_news = await self.news_processor.process_news(
                important_news, glossary
            )
            logger.info(f"🎉 生成 {len(processed_news)} 則處理後新聞")

            if not processed_news:
                logger.info("ℹ️ 沒有需要推播的新聞")
                await self.line_notifier.send_push_message_text(
                    target_id, "太棒了！您已掌握所有最新動態，目前沒有更多新聞。"
                )
                return

            # (步驟 5)
            logger.info("📱 開始發送 LINE 通知...")
            await self.line_notifier.send_news_batch(processed_news, target_id)

            # (步驟 6)
            logger.info("💾 記錄發送結果到 Google Sheets...")
            await self.sheet_manager.log_sent_news(processed_news)

            logger.info(f"🎊 新聞流水線執行完成！已發送至 {target_id}")

        except Exception as e:
            logger.error(f"💥 流水線執行失敗: {e}", exc_info=True)
            await _async_send_discord_alert(
                self.http_client, f"Pipeline 執行失敗 (Target: {target_id})", e
            )
            try:
                error_message = f"抱歉，處理新聞時發生錯誤。\n錯誤: {str(e)[:50]}...\n我們已收到通知並將盡快修復。"
                await self.line_notifier.send_push_message_text(
                    target_id, error_message
                )
            except Exception as notify_e:
                logger.error(f"連錯誤通知都發不出去: {notify_e}")


# ==============================================================================
# 全域實例與初始化 (保持不變)
# ==============================================================================

try:
    news_bot = NewsBot(config)
except Exception as e:
    logger.critical(f"NewsBot 實例化失敗，無法啟動服務: {e}", exc_info=True)
    _sync_send_discord_alert("NewsBot 實例化失敗，服務無法啟動", e)
    sys.exit(1)

handler: Optional[WebhookHandler] = None
_bot_initialized = False


def initialize_bot_globally():
    """在 worker 啟動後初始化 (邏輯保持不變)"""
    global _bot_initialized, handler
    if _bot_initialized:
        return

    logger.info("Gunicorn worker 啟動，正在初始化 NewsBot 組件...")
    try:
        channel_secret = os.getenv("LINE_CHANNEL_SECRET")
        if not channel_secret:
            raise ValueError("LINE_CHANNEL_SECRET 未設定")

        handler = WebhookHandler(channel_secret)
        logger.info("Webhook Handler 初始化成功")

        handler.add(MessageEvent, message=TextMessageContent)(handle_message)
        logger.info("Webhook 事件處理器註冊成功")

        news_bot._initialize_components()
        _bot_initialized = True
        logger.info("NewsBot 組件初始化成功 (Worker)")

        def shutdown_client():
            if _bot_initialized and news_bot and news_bot.http_client:
                logger.info("Gunicorn worker 正在關閉，開始關閉 HTTP client...")
                try:
                    asyncio.run(news_bot.http_client.close())
                    logger.info("HTTP client 已成功關閉。")
                except Exception as e:
                    logger.error(f"關閉 HTTP client 時發生錯誤: {e}")

        atexit.register(shutdown_client)
        logger.info("已註冊 atexit shutdown hook 用於關閉 HTTP client。")

    except Exception as e:
        logger.critical(f"Gunicorn worker 初始化失敗: {e}", exc_info=True)
        _sync_send_discord_alert("Gunicorn worker 初始化失敗，服務可能無法啟動", e)
        raise


# [v3.6] 使用 FastAPI 啟動事件
@app.on_event("startup")
async def startup_event():
    """FastAPI 啟動時初始化"""
    initialize_bot_globally()


# ==============================================================================
# API 端點 (FastAPI 版本)
# ==============================================================================


# [v3.6] 入口 A: LINE 使用者觸發 (FastAPI 語法)
@app.post("/callback")
async def callback(
    request: Request, x_line_signature: str = Header(None, alias="X-Line-Signature")
):
    """處理來自 LINE 的 Webhook 事件"""
    # initialize_bot_globally() # 註：已改用 startup_event 觸發

    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")

    logger.info(f"收到來自 LINE 的 Webhook 請求 - Body: {body}")

    if not body:
        logger.info(
            "收到了空的請求 body，可能是來自 LINE 後台的 Verify 請求，直接回傳 OK。"
        )
        return PlainTextResponse("OK")

    try:
        handler.handle(body, x_line_signature)
    except InvalidSignatureError:
        logger.error("Webhook 簽名驗證失敗，請檢查您的 Channel Secret。")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except ApiException as e:
        logger.error(f"處理 LINE Webhook 時發生 API 錯誤: {e.body}")
        raise HTTPException(status_code=500, detail="LINE API error")
    except Exception as e:
        logger.error(f"處理 Webhook 時發生未知錯誤: {e}", exc_info=True)
        await _async_send_discord_alert(
            news_bot.http_client, "Webhook /callback 發生嚴重錯誤", e
        )
        raise HTTPException(status_code=500, detail="Internal server error")

    return PlainTextResponse("OK")


# (handle_message 保持不變，它不是 API 端點)
async def handle_message(event):
    """處理文字訊息事件"""
    # initialize_bot_globally() # 註：已在啟動時完成

    text = event.message.text.strip()

    if text in TRIGGER_WORDS:
        source_id = (
            event.source.user_id
            if event.source.type == "user"
            else event.source.group_id
        )
        if not source_id:
            source_id = event.source.user_id

        logger.info(f"收到觸發指令 '{text}'，來自 ID: {source_id}")

        try:
            await news_bot.line_notifier.send_reply_message_text(
                event.reply_token,
                "✅ 收到指令！正在為您準備最新國際新聞，過程約需 40-60 秒，請稍候...",
            )

            logger.info(f"正在為 {source_id} 啟動背景新聞處理任務...")
            asyncio.create_task(news_bot.run_pipeline(target_id=source_id))

        except Exception as e:
            logger.error(
                f"handle_message 失敗 (Source: {source_id}): {e}", exc_info=True
            )
            await _async_send_discord_alert(
                news_bot.http_client, f"handle_message 失敗 (Source: {source_id})", e
            )
            try:
                await news_bot.line_notifier.send_push_message_text(
                    source_id,
                    f"抱歉，啟動新聞處理時發生錯誤。\n我們已收到通知並將盡快修復。",
                )
            except Exception as push_e:
                logger.error(f"連錯誤通知都發不出去: {push_e}")
    else:
        logger.info(f"忽略非觸發詞 '{text}'，來自 ID: {event.source.user_id}")


# [v3.6] 入口 B: n8n 定時觸發 (FastAPI 語法)
@app.post("/trigger-push")
async def trigger_push(authorization: str = Header(None, alias="Authorization")):
    """處理來自 n8n 的定時推播請求"""
    # initialize_bot_globally() # 註：已在啟動時完成

    secret_key = os.getenv("TRIGGER_SECRET_KEY")
    if not secret_key or authorization != f"Bearer {secret_key}":
        logger.warning("收到未經授權的 trigger-push 請求")
        raise HTTPException(status_code=401, detail="Unauthorized")

    default_target_id = os.getenv("USER_ID")
    if not default_target_id:
        logger.error("環境變數 USER_ID 未設定，無法執行定時推播")
        await _async_send_discord_alert(
            news_bot.http_client, "trigger-push 失敗：USER_ID 未設定", None
        )
        raise HTTPException(status_code=500, detail="USER_ID not configured")

    logger.info(f"收到 n8n 定時觸發指令，將推播至預設 ID: {default_target_id}")

    try:
        asyncio.create_task(news_bot.run_pipeline(target_id=default_target_id))
        return PlainTextResponse("OK: News pipeline triggered for default user.")
    except Exception as e:
        logger.error(f"啟動 /trigger-push 任務失敗: {e}", exc_info=True)
        await _async_send_discord_alert(
            news_bot.http_client, "啟動 /trigger-push 任務失敗", e
        )
        raise HTTPException(status_code=500, detail="Failed to create background task")


# [v3.6] 健康檢查端點 (FastAPI 語法)
@app.get("/")
async def health_check():
    """健康檢查端點"""
    # initialize_bot_globally() # 註：已在啟動時完成
    return PlainTextResponse(
        f"{config['app']['name']} v{config['app']['version']} is running. "
        f"Bot initialized: {_bot_initialized}"
    )


# ==============================================================================
# 本地開發伺服器啟動
# ==============================================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    logger.info(f"啟動 Uvicorn 開發伺服器於 http://0.0.0.0:{port}")

    # [v3.6]
    # Uvicorn 啟動時會自動觸發 @app.on_event("startup")
    # 我們不再需要手動呼叫 initialize_bot_globally()
    # try:
    #     initialize_bot_globally()
    # except Exception as e:
    #     logger.critical(f"本地啟動時初始化失敗: {e}", exc_info=True)
    #     sys.exit(1)

    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)

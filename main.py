# main.py
# -*- coding: utf-8 -*-
"""
LINE Bot 新聞推播系統 - v3.6.1 (FastAPI + 日誌修復)
"""

# 本地開發時載入 .env 檔案（Cloud Run 會直接注入環境變數）
from dotenv import load_dotenv

load_dotenv(verbose=True)

import sys
import os

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import asyncio
import traceback
from typing import Dict, Optional
import atexit
import logging  # ❗️ [v3.6.1] 導入 logging

import requests

# FastAPI 導入
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import PlainTextResponse

# LINE Bot SDK
from linebot.v3.webhook import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import ApiException
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# 核心組件
from utils.config_manager import ConfigManager
from utils.http_client import AsyncHTTPClient
from core import NewsFetcher, NewsClassifier, NewsProcessor, LineNotifier, SheetManager
from utils.logger import get_logger

# ==============================================================================
# 全域設定與初始化
# ==============================================================================

config = ConfigManager().load_config()

# ❗️ [v3.6.1 修正] 手動設定日誌系統
# FastAPI/Uvicorn 預設不會完整設定 root logger
# 導致所有 logger.info/error 都被丟進虛無
log_level_str = config.get("app", {}).get("log_level", "INFO")
log_level = logging.getLevelName(log_level_str.upper())
log_formatter = logging.Formatter(
    "%(asctime)s | %(name)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
stream_handler = logging.StreamHandler(sys.stderr)
stream_handler.setFormatter(log_formatter)

root_logger = logging.getLogger()
root_logger.setLevel(log_level)
if not root_logger.hasHandlers():  # 避免重複添加
    root_logger.addHandler(stream_handler)
# --- [日誌修正完畢] ---

logger = get_logger(__name__)

# 定義觸發詞
TRIGGER_WORDS = ["🆕", "news", "News", "NEWS"]

# 建立 FastAPI app
app = FastAPI(
    title=config.get("app", {}).get("name", "LINE Bot"),
    version=config.get("app", {}).get("version", "3.6.1"),
)

# ==============================================================================
# 警報函式
# ==============================================================================


def _build_alert_payload(message: str, error: Exception = None) -> Optional[Dict]:
    """建立警報的 payload"""
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
    """同步警報（啟動時使用）"""
    alert_data = _build_alert_payload(message, error)
    if not alert_data:
        logger.warning("DISCORD_WEBHOOK_URL: 未設定。跳過警報。")
        return

    try:
        requests.post(alert_data["webhook_url"], json=alert_data["payload"], timeout=5)
    except Exception as e:
        logger.error(f"發送 SYNC Discord 警報失敗: {e}")


async def _async_send_discord_alert(
    http_client: AsyncHTTPClient, message: str, error: Exception = None
):
    """非同步警報（執行時使用）"""
    alert_data = _build_alert_payload(message, error)
    if not http_client or not alert_data:
        return

    try:
        await http_client.post_json(
            alert_data["webhook_url"], json_payload=alert_data["payload"]
        )
    except Exception as e:
        logger.error(f"發送 ASYNC Discord 警報失敗: {e}")


# ==============================================================================
# NewsBot 主類別
# ==============================================================================


class NewsBot:
    """新聞機器人主控制器"""

    def __init__(self, config: Dict):
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
        """執行完整的新聞處理流水線"""
        logger.info("=" * 50)
        logger.info(f"🚀 啟動新聞處理流水線 (目標: {target_id})")
        logger.info("=" * 50)

        try:
            logger.info("📊 初始化 Google Sheets 連線...")
            await self.sheet_manager.initialize()

            glossary = await self.sheet_manager.load_glossary()
            logger.info(f"📚 載入術語表: {len(glossary)} 條術語")
            sent_links = await self.sheet_manager.get_sent_links()
            logger.info(f"📝 載入已發送記錄: {len(sent_links)} 條")

            logger.info("📰 開始抓取新聞來源...")
            all_news = await self.news_fetcher.fetch_all_news()

            logger.info("🎯 開始新聞分類與過濾...")
            important_news = await self.news_classifier.classify_and_filter(
                all_news, sent_links
            )
            logger.info(f"✨ 過濾出 {len(important_news)} 則重要新聞")

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

            logger.info("📱 開始發送 LINE 通知...")
            await self.line_notifier.send_news_batch(processed_news, target_id)

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
# 全域實例與初始化
# ==============================================================================

try:
    news_bot = NewsBot(config)
except Exception as e:
    logger.critical(f"NewsBot 實例化失敗: {e}", exc_info=True)
    _sync_send_discord_alert("NewsBot 實例化失敗", e)
    sys.exit(1)

handler: Optional[WebhookHandler] = None
_bot_initialized = False


def initialize_bot_globally():
    """在 worker 啟動後初始化"""
    global _bot_initialized, handler
    if _bot_initialized:
        return

    logger.info("正在初始化 NewsBot 組件...")
    try:
        channel_secret = os.getenv("LINE_CHANNEL_SECRET")
        if not channel_secret:
            raise ValueError("LINE_CHANNEL_SECRET 未設定")

        handler = WebhookHandler(channel_secret)
        logger.info("Webhook Handler 初始化成功")

        # 註冊事件處理器
        handler.add(MessageEvent, message=TextMessageContent)(handle_message)
        logger.info("Webhook 事件處理器註冊成功")

        news_bot._initialize_components()
        _bot_initialized = True
        logger.info("NewsBot 組件初始化成功")

        # 註冊關閉掛鉤
        def shutdown_client():
            if _bot_initialized and news_bot and news_bot.http_client:
                logger.info("正在關閉 HTTP client...")
                try:
                    asyncio.run(news_bot.http_client.close())
                    logger.info("HTTP client 已成功關閉")
                except Exception as e:
                    logger.error(f"關閉 HTTP client 時發生錯誤: {e}")

        atexit.register(shutdown_client)
        logger.info("已註冊 atexit shutdown hook")

    except Exception as e:
        logger.critical(f"初始化失敗: {e}", exc_info=True)
        _sync_send_discord_alert("初始化失敗", e)
        raise


# FastAPI 啟動事件
@app.on_event("startup")
async def startup_event():
    """FastAPI 啟動時初始化"""
    initialize_bot_globally()


# ==============================================================================
# API 端點
# ==============================================================================


@app.post("/callback")
async def callback(
    request: Request, x_line_signature: str = Header(None, alias="X-Line-Signature")
):
    """處理 LINE Webhook"""
    initialize_bot_globally()

    body = await request.body()
    body_str = body.decode("utf-8")

    logger.info(f"收到 LINE Webhook - Body: {body_str}")

    if not body_str:
        logger.info("收到空請求，可能是 Verify 請求")
        return PlainTextResponse("OK")

    try:
        handler.handle(body_str, x_line_signature)
    except InvalidSignatureError:
        logger.error("Webhook 簽名驗證失敗")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except ApiException as e:
        logger.error(f"LINE API 錯誤: {e.body}")
        raise HTTPException(status_code=500, detail="LINE API error")
    except Exception as e:
        logger.error(f"Webhook 處理錯誤: {e}", exc_info=True)
        await _async_send_discord_alert(news_bot.http_client, "Webhook 錯誤", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return PlainTextResponse("OK")


async def handle_message(event):
    """處理文字訊息"""
    initialize_bot_globally()

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

        # ❗️ [v3.6.1 修正] reply_token 會快速過期
        # 必須立刻啟動回覆任務，不能等待它完成
        # 將「回覆」和「管線」作為兩個獨立的背景任務

        reply_task = asyncio.create_task(
            news_bot.line_notifier.send_reply_message_text(
                event.reply_token,
                "✅ 收到指令！正在為您準備最新國際新聞，過程約需 40-60 秒，請稍候...",
            )
        )

        pipeline_task = asyncio.create_task(news_bot.run_pipeline(target_id=source_id))

        # 監控回覆任務（但不阻塞）
        try:
            await reply_task
        except Exception as e:
            logger.error(f"回覆任務失敗: {e}", exc_info=True)
            await _async_send_discord_alert(
                news_bot.http_client, f"回覆任務失敗 (Source: {source_id})", e
            )

        # 不 await pipeline_task，讓它在背景執行
        # run_pipeline 內部有自己的錯誤處理

    else:
        logger.info(f"忽略非觸發詞 '{text}'")


@app.post("/trigger-push")
async def trigger_push(authorization: str = Header(None)):
    """處理定時推播"""
    initialize_bot_globally()

    secret_key = os.getenv("TRIGGER_SECRET_KEY")
    if not secret_key or authorization != f"Bearer {secret_key}":
        logger.warning("收到未經授權的 trigger-push 請求")
        raise HTTPException(status_code=401, detail="Unauthorized")

    default_target_id = os.getenv("USER_ID")
    if not default_target_id:
        logger.error("USER_ID 未設定")
        await _async_send_discord_alert(
            news_bot.http_client, "trigger-push 失敗：USER_ID 未設定", None
        )
        raise HTTPException(status_code=500, detail="USER_ID not configured")

    logger.info(f"收到定時觸發，推播至: {default_target_id}")

    try:
        asyncio.create_task(news_bot.run_pipeline(target_id=default_target_id))
        return PlainTextResponse("OK: News pipeline triggered")
    except Exception as e:
        logger.error(f"啟動 trigger-push 失敗: {e}", exc_info=True)
        await _async_send_discord_alert(news_bot.http_client, "trigger-push 失敗", e)
        raise HTTPException(status_code=500, detail="Failed to trigger pipeline")


@app.get("/")
async def health_check():
    """健康檢查"""
    initialize_bot_globally()
    return PlainTextResponse(
        f"{config['app']['name']} v{config['app']['version']} is running. "
        f"Bot initialized: {_bot_initialized}"
    )


# ==============================================================================
# 本地開發啟動
# ==============================================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    logger.info(f"啟動 Uvicorn 開發伺服器於 http://0.0.0.0:{port}")

    try:
        initialize_bot_globally()
    except Exception as e:
        logger.critical(f"本地啟動失敗: {e}", exc_info=True)
        sys.exit(1)

    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)

# core/line_notifier.py
# -*- coding: utf-8 -*-
"""
LINE 通知器
負責組裝 Flex Message 並透過 LINE API 發送
"""

import os
from typing import Dict, List, Any

# --- ❗️【這就是最終的修復】---
#
# 導入 SDK 基礎設施 (來自頂層)
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    ApiClient,
    PushMessageRequest,
    ReplyMessageRequest
)

# 導入 Message 物件 (來自 .models 子模組)
from linebot.v3.messaging.models import (
    TextSendMessage,
    FlexSendMessage
)

# 導入 Flex Message 元件 (來自頂層)
from linebot.v3.messaging import (
    BubbleContainer,
    BoxComponent,
    TextComponent,
    ButtonComponent,
    URIAction,
    CarouselContainer
)
# --- ❗️【修復完畢】---

from utils.logger import get_logger

logger = get_logger(__name__)

class LineNotifier:
    """LINE 通知器，負責發送 LINE 訊息"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config.get('line_bot', {})
        
        # ❗️ 從環境變數讀取 LINE Bot 憑證
        access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
        if not access_token:
            logger.error("環境變數 LINE_CHANNEL_ACCESS_TOKEN 未設定")
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN 未設定")

        self.configuration = Configuration(access_token=access_token)
        
        # ❗️【修改】使用 AsyncMessagingApi 來進行非同步發送
        self.async_api_client = AsyncApiClient(self.configuration)
        self.line_bot_api = AsyncMessagingApi(self.async_api_client)

    async def send_reply_message_text(self, reply_token: str, text: str) -> None:
        """
        (免費) 回覆文字訊息
        用於 Webhook 立即回覆「處理中」
        """
        try:
            reply_request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextSendMessage(text=text)]
            )
            await self.line_bot_api.reply_message(reply_request)
            logger.info(f"已回覆訊息至 token: {reply_token[:10]}...")
        except Exception as e:
            logger.error(f"回覆訊息失敗: {e}")

    async def send_push_message_text(self, target_id: str, text: str) -> None:
        """
        (付費) 主動推播文字訊息
        用於發送錯誤通知或「沒有新新聞」
        """
        try:
            push_request = PushMessageRequest(
                to=target_id,
                messages=[TextSendMessage(text=text)]
            )
            await self.line_bot_api.push_message(push_request)
            logger.info(f"已推播文字訊息至: {target_id}")
        except Exception as e:
            logger.error(f"推播文字訊息失敗: {e}")

    async def send_flex_message(self, target_id: str, alt_text: str, container: Any) -> None:
        """
        (付費) 主動推播 Flex Message
        """
        try:
            message = FlexSendMessage(
                alt_text=alt_text,
                contents=container
            )
            push_request = PushMessageRequest(
                to=target_id,
                messages=[message]
            )
            await self.line_bot_api.push_message(push_request)
            logger.info(f"已推播 Flex Message 至: {target_id}")
        except Exception as e:
            logger.error(f"推播 Flex Message 失敗: {e}")

    def _create_news_bubble(self, news_item: Dict[str, str]) -> BubbleContainer:
        """
        (內部) 建立單一新聞的 Flex Message 泡泡
        """
        return BubbleContainer(
            header=BoxComponent(
                layout='vertical',
                contents=[
                    TextComponent(
                        text=news_item.get('source', 'News'),
                        weight='bold',
                        color='#AAAAAA',
                        size='sm'
                    ),
                    TextComponent(
                        text=news_item.get('title', 'No Title'),
                        weight='bold',
                        size='xl',
                        margin='md',
                        wrap=True
                    )
                ]
            ),
            body=BoxComponent(
                layout='vertical',
                contents=[
                    TextComponent(
                        text=news_item.get('summary', 'No summary available.'),
                        wrap=True,
                        size='sm',
                        margin='md'
                    )
                ]
            ),
            footer=BoxComponent(
                layout='vertical',
                spacing='sm',
                contents=[
                    ButtonComponent(
                        style='link',
                        height='sm',
                        action=URIAction(
                            label='閱讀原文',
                            uri=news_item.get('link', '#')
                        )
                    )
                ]
            )
        )

    async def send_news_batch(self, news_items: List[Dict[str, str]], target_id: str) -> None:
        """
        (核心) 建立並發送新聞輪播
        """
        if not news_items:
            logger.info("沒有新聞可發送")
            return

        # 建立多個泡泡
        bubbles = [self._create_news_bubble(item) for item in news_items]
        
        # 建立輪播容器
        # ❗️ LINE 限制輪播一次最多 12 則
        carousel_container = CarouselContainer(contents=bubbles[:12])
        
        await self.send_flex_message(
            target_id=target_id,
            alt_text=f"您有 {len(bubbles)} 則最新國際新聞",
            container=carousel_container
        )

    def __del__(self):
        """
        (非同步) 關閉 AsyncApiClient
        """
        if hasattr(self, 'async_api_client'):
            # 在非同步環境中，正確的關閉方式是 await
            # 但在 __del__ 中，我們只能盡力而為
            try:
                # 嘗試建立一個事件迴圈來關閉
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.async_api_client.close())
                except RuntimeError:
                    # 沒有正在運行的迴圈，只好用新的
                    asyncio.run(self.async_api_client.close())
            except Exception:
                pass # 忽略關閉時的錯誤
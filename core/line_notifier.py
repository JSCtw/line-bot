# core/line_notifier.py (Flex Message V3 SDK 改造+同時支援 Push 和 Reply+正確的 import 路徑，徹底解決 ImportError+正確的 send_reply_message_text 方法)
# -*- coding: utf-8 -*-
"""
LINE 通知器
負責將處理好的新聞以 Flex Message 格式推播到 LINE
"""

import os
from typing import List, Dict, Any

# ❗️【最終校對】根據 line-bot-sdk v3.x 的標準結構，精確劃分 import 來源
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    FlexMessage,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
    ApiException
)
from linebot.v3.messaging.models import (
    CarouselContainer,
    BubbleContainer,
    BoxComponent,
    TextComponent,
    SeparatorComponent,
    ButtonComponent,
    URIAction
)

from utils.logger import get_logger, log_async_execution_time

logger = get_logger(__name__)

class LineNotifier:
    """LINE 推播通知器 - 使用 Flex Message Carousel"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get('line_bot', {})
        self.access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
        
        if not self.access_token:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN 環境變數未設定")
            
        configuration = Configuration(access_token=self.access_token)
        self.api_client = AsyncApiClient(configuration)
        self.line_bot_api = AsyncMessagingApi(self.api_client)
        
        logger.info("✅ LINE Bot API (Async V3) 初始化成功")

    @log_async_execution_time("send_news_batch")
    async def send_news_batch(self, news_list: List[Dict], target_id: str) -> int:
        """將新聞列表以 Flex Message Carousel 的形式批次發送。"""
        if not news_list:
            logger.info("📭 沒有新聞需要發送")
            return 0
            
        logger.info(f"📱 準備發送 {len(news_list)} 則新聞到 LINE (ID: {target_id})...")
        
        try:
            carousel_container = self._create_news_carousel(news_list)
            
            message = FlexMessage(
                alt_text='為您送上今日新聞精選',
                contents=carousel_container
            )
            
            push_request = PushMessageRequest(
                to=target_id,
                messages=[message]
            )

            await self.line_bot_api.push_message(push_request)
            
            logger.info(f"🎉 批次發送完成！成功發送 1 則 Carousel 包含 {len(news_list)} 則新聞。")
            return len(news_list)

        except ApiException as e:
            logger.error(f"❌ 發送 LINE 通知時發生 API 錯誤: {e.status}")
            logger.error(f"錯誤訊息: {e.body}")
            return 0
        except Exception as e:
            logger.error(f"❌ 發送 LINE 通知時發生未知錯誤: {e}", exc_info=True)
            return 0

    def _create_news_carousel(self, news_list: List[Dict]) -> CarouselContainer:
        """根據新聞列表，使用 SDK 物件建立一個 Flex Carousel。"""
        bubbles = []
        for news in news_list:
            source_name = news.get('primary_source', '新聞來源')
            
            bubble = BubbleContainer(
                size="giga",
                header=BoxComponent(
                    layout="vertical",
                    contents=[
                        TextComponent(
                            text=f"📰 來源: {source_name}",
                            color="#999999",
                            size="sm",
                            wrap=True
                        )
                    ],
                    padding_bottom="md"
                ),
                body=BoxComponent(
                    layout="vertical",
                    contents=[
                        TextComponent(
                            text=news.get('title', '無標題'),
                            weight="bold",
                            size="lg",
                            wrap=True
                        ),
                        SeparatorComponent(margin="md"),
                        TextComponent(
                            text=news.get('summary', '無法產生摘要'),
                            wrap=True,
                            size="sm",
                            margin="md",
                            color="#666666"
                        )
                    ]
                ),
                footer=BoxComponent(
                    layout="vertical",
                    spacing="sm",
                    contents=[
                        ButtonComponent(
                            style="link",
                            height="sm",
                            action=URIAction(
                                label=f"前往 {source_name} 閱讀全文",
                                uri=news.get('link', 'https://www.google.com')
                            )
                        )
                    ]
                )
            )
            bubbles.append(bubble)

        return CarouselContainer(contents=bubbles)

    async def send_reply_message_text(self, reply_token: str, text: str):
        """發送純文字的回覆訊息 (用於 Webhook 的立即回覆)。"""
        try:
            reply_request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
            )
            await self.line_bot_api.reply_message(reply_request)
            logger.info(f"✅ 已發送即時回覆訊息: '{text}'")
        except ApiException as e:
            logger.error(f"❌ 發送 LINE 回覆訊息失敗: {e.body}")
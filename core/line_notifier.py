# core/line_notifier.py (v3.35 - from_json 版本)
# -*- coding: utf-8 -*-
"""
LINE 通知器
負責組裝 Flex Message 並透過 LINE API 發送
使用 FlexContainer.from_json() 方法，100% 避免 Import 問題
"""

import os
import json
from typing import Dict, List, Any

# --- V3 正確的 Import 結構 ---
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    TextMessage,
    FlexMessage,
    FlexContainer  # 關鍵：用於 from_json
)

from linebot.v3.messaging.models import (
    PushMessageRequest,
    ReplyMessageRequest
)

from utils.logger import get_logger

logger = get_logger(__name__)


class LineNotifier:
    """LINE 通知器，負責發送 LINE 訊息"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config.get('line_bot', {})
        
        # 從環境變數讀取 LINE Bot 憑證
        access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
        if not access_token:
            logger.error("環境變數 LINE_CHANNEL_ACCESS_TOKEN 未設定")
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN 未設定")

        self.configuration = Configuration(access_token=access_token)
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
                messages=[TextMessage(text=text)]
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
                messages=[TextMessage(text=text)]
            )
            await self.line_bot_api.push_message(push_request)
            logger.info(f"已推播文字訊息至: {target_id}")
        except Exception as e:
            logger.error(f"推播文字訊息失敗: {e}")

    async def send_flex_message(self, target_id: str, alt_text: str, container: FlexContainer) -> None:
        """
        (付費) 主動推播 Flex Message
        """
        try:
            message = FlexMessage(
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

    def _create_news_bubble_json(self, news_item: Dict[str, str]) -> Dict:
        """
        (內部) 建立單一新聞的 Flex Message Bubble JSON
        
        這個 JSON 結構來自 LINE Flex Message Simulator
        你可以在這裡客製化樣式：https://developers.line.biz/flex-simulator/
        """
        return {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": news_item.get('source', 'News'),
                        "weight": "bold",
                        "color": "#AAAAAA",
                        "size": "sm"
                    },
                    {
                        "type": "text",
                        "text": news_item.get('title', 'No Title'),
                        "weight": "bold",
                        "size": "xl",
                        "margin": "md",
                        "wrap": True
                    }
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": news_item.get('summary', 'No summary available.'),
                        "wrap": True,
                        "size": "sm",
                        "margin": "md"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {
                            "type": "uri",
                            "label": "閱讀原文",
                            "uri": news_item.get('link', 'https://example.com')
                        }
                    }
                ]
            }
        }

    async def send_news_batch(self, news_items: List[Dict[str, str]], target_id: str) -> None:
        """
        (核心) 建立並發送新聞輪播
        
        使用 FlexContainer.from_json() 方法，100% 避免 Import 問題
        """
        if not news_items:
            logger.info("沒有新聞可發送")
            return

        try:
            # 建立多個 Bubble JSON（最多 12 則）
            bubbles_json = [
                self._create_news_bubble_json(item) 
                for item in news_items[:12]
            ]
            
            # 建立 Carousel JSON
            carousel_json = {
                "type": "carousel",
                "contents": bubbles_json
            }
            
            # 使用 from_json 轉換為 FlexContainer
            carousel_container = FlexContainer.from_json(json.dumps(carousel_json))
            
            # 發送
            await self.send_flex_message(
                target_id=target_id,
                alt_text=f"您有 {len(bubbles_json)} 則最新國際新聞",
                container=carousel_container
            )
            
            logger.info(f"成功發送 {len(bubbles_json)} 則新聞至 {target_id}")
            
        except Exception as e:
            logger.error(f"發送新聞批次失敗: {e}", exc_info=True)
            # 降級方案：發送純文字通知
            await self.send_push_message_text(
                target_id=target_id,
                text=f"❌ 新聞發送失敗\n\n共有 {len(news_items)} 則新聞，但傳送時發生錯誤。請稍後再試。"
            )
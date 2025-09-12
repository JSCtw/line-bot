# -*- coding: utf-8 -*-
"""
LINE 通知器
負責格式化和發送 LINE 訊息，支援批次發送和訊息範本管理
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

from linebot.v3.messaging import MessagingApi, Configuration, ApiClient
from linebot.v3.messaging.models import TextMessage, PushMessageRequest
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.logger import log_async_execution_time

logger = logging.getLogger(__name__)

class LineNotifier:
    """LINE 通知器 - 負責 LINE 訊息的格式化和發送"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.line_config = config.get('line_bot', {})
        
        # LINE Bot 設定
        self.message_delay = self.line_config.get('message_delay', 1.0)
        self.header_templates = self.line_config.get('header_templates', {})
        self.time_periods = self.line_config.get('time_periods', {})
        
        # 台灣時區
        self.timezone = timezone(timedelta(hours=8))
        
        # 環境變數
        self.access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
        self.user_id = os.getenv("USER_ID")
        
        # 驗證必要設定
        if not self.access_token:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN 環境變數未設定")
        if not self.user_id:
            raise ValueError("USER_ID 環境變數未設定")
        
        # 初始化 LINE Bot API
        self._initialize_line_api()
        
        # 線程池用於同步 API 呼叫
        self.executor = ThreadPoolExecutor(max_workers=2)
    
    def _initialize_line_api(self) -> None:
        """初始化 LINE Bot API"""
        try:
            configuration = Configuration(access_token=self.access_token)
            api_client = ApiClient(configuration)
            self.line_bot_api = MessagingApi(api_client)
            logger.info("✅ LINE Bot API 初始化成功")
        except Exception as e:
            logger.error(f"❌ LINE Bot API 初始化失敗: {e}")
            raise
    
    @log_async_execution_time()
    async def send_news_batch(self, news_items: List[Dict[str, str]]) -> int:
        """
        批次發送新聞到 LINE
        
        Args:
            news_items: 新聞項目列表
            
        Returns:
            成功發送的訊息數量
        """
        if not news_items:
            logger.info("📭 沒有新聞需要發送")
            return 0
        
        logger.info(f"📱 準備發送 {len(news_items)} 則新聞到 LINE...")
        
        sent_count = 0
        
        try:
            # 發送標題訊息
            header_sent = await self._send_header_message()
            if header_sent:
                sent_count += 1
            
            # 發送新聞內容
            for i, news_item in enumerate(news_items, 1):
                try:
                    success = await self._send_news_item(news_item, i)
                    if success:
                        sent_count += 1
                        logger.info(f"✅ 第 {i}/{len(news_items)} 則新聞發送成功")
                    else:
                        logger.warning(f"⚠️ 第 {i}/{len(news_items)} 則新聞發送失敗")
                    
                    # 訊息間延遲（避免限流）
                    if i < len(news_items):
                        await asyncio.sleep(self.message_delay)
                        
                except Exception as e:
                    logger.error(f"❌ 第 {i} 則新聞發送時發生錯誤: {e}")
            
            logger.info(f"🎉 批次發送完成！成功: {sent_count-1}/{len(news_items)} 則新聞")
            
        except Exception as e:
            logger.error(f"❌ 批次發送過程發生錯誤: {e}")
        
        return sent_count
    
    async def _send_header_message(self) -> bool:
        """發送標題訊息"""
        try:
            header_text = self._generate_header_message()
            return await self._send_line_message(header_text)
        except Exception as e:
            logger.error(f"❌ 標題訊息發送失敗: {e}")
            return False
    
    def _generate_header_message(self) -> str:
        """生成標題訊息"""
        now = datetime.now(self.timezone)
        date_str = now.strftime("%Y/%m/%d")
        hour = now.hour
        
        # 判斷時間段
        time_period_key = "evening"  # 預設
        for period, (start, end) in self.time_periods.items():
            if start <= end:  # 正常時間範圍
                if start <= hour < end:
                    time_period_key = period
                    break
            else:  # 跨日時間範圍 (如晚間 18:00 - 次日 5:00)
                if hour >= start or hour < end:
                    time_period_key = period
                    break
        
        # 獲取對應範本
        template = self.header_templates.get(
            time_period_key, 
            "🆕 {date} 國際新聞推播"
        )
        
        return template.format(date=date_str)
    
    async def _send_news_item(self, news_item: Dict[str, str], index: int) -> bool:
        """發送單則新聞"""
        try:
            message_text = self._format_news_message(news_item, index)
            return await self._send_line_message(message_text)
        except Exception as e:
            logger.error(f"❌ 新聞項目發送失敗: {e}")
            return False
    
    def _format_news_message(self, news_item: Dict[str, str], index: Optional[int] = None) -> str:
        """格式化新聞訊息"""
        title = news_item.get('title', '無標題')
        summary = news_item.get('summary', '無摘要')
        link = news_item.get('link', '')
        
        # 基本格式
        message_parts = [
            f"【{title}】",
            "",
            summary
        ]
        
        # 添加連結（如果有）
        if link:
            message_parts.extend(["", link])
        
        # 添加來源資訊（如果有）
        if 'primary_source' in news_item:
            source = news_item['primary_source']
            sources_count = news_item.get('sources_count', 1)
            if sources_count > 1:
                message_parts.append(f"\n📊 來源: {source} 等 {sources_count} 個媒體")
            else:
                message_parts.append(f"\n📰 來源: {source}")
        
        return "\n".join(message_parts)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((Exception,))
    )
    async def _send_line_message(self, message_text: str) -> bool:
        """發送單一 LINE 訊息（帶重試機制）"""
        try:
            # 在線程池中執行同步的 LINE API 呼叫
            await asyncio.get_event_loop().run_in_executor(
                self.executor, self._sync_send_message, message_text
            )
            return True
        except Exception as e:
            logger.error(f"❌ LINE 訊息發送失敗: {e}")
            raise
    
    def _sync_send_message(self, message_text: str) -> None:
        """同步發送 LINE 訊息"""
        try:
            # 檢查訊息長度（LINE 限制為 5000 字元）
            if len(message_text) > 5000:
                message_text = message_text[:4900] + "\n\n... (訊息過長已截斷)"
                logger.warning("⚠️ 訊息過長，已自動截斷")
            
            # 發送訊息
            push_request = PushMessageRequest(
                to=self.user_id,
                messages=[TextMessage(text=message_text)]
            )
            
            self.line_bot_api.push_message(push_request)
            
        except Exception as e:
            logger.error(f"❌ 同步 LINE 訊息發送失敗: {e}")
            raise
    
    async def send_test_message(self) -> bool:
        """發送測試訊息（用於健康檢查）"""
        try:
            test_message = f"🔧 LINE Bot 測試訊息\n時間: {datetime.now(self.timezone).strftime('%Y-%m-%d %H:%M:%S')}"
            return await self._send_line_message(test_message)
        except Exception as e:
            logger.error(f"❌ 測試訊息發送失敗: {e}")
            return False
    
    async def send_error_notification(self, error_message: str) -> bool:
        """發送錯誤通知"""
        try:
            notification = f"⚠️ 系統錯誤通知\n\n{error_message}\n\n時間: {datetime.now(self.timezone).strftime('%Y-%m-%d %H:%M:%S')}"
            return await self._send_line_message(notification)
        except Exception as e:
            logger.error(f"❌ 錯誤通知發送失敗: {e}")
            return False
    
    async def send_system_report(self, report_data: Dict[str, Any]) -> bool:
        """發送系統執行報告"""
        try:
            report_message = self._format_system_report(report_data)
            return await self._send_line_message(report_message)
        except Exception as e:
            logger.error(f"❌ 系統報告發送失敗: {e}")
            return False
    
    def _format_system_report(self, report_data: Dict[str, Any]) -> str:
        """格式化系統報告"""
        status_icon = "✅" if report_data.get('success', False) else "❌"
        
        report_parts = [
            f"{status_icon} 系統執行報告",
            "=" * 20,
            f"📊 處理文章: {report_data.get('processed_count', 0)} 篇",
            f"📱 發送訊息: {report_data.get('sent_count', 0)} 則",
            f"⏱️ 執行時間: {report_data.get('execution_time', 0):.1f} 秒",
        ]
        
        if report_data.get('errors'):
            report_parts.extend([
                "",
                "⚠️ 錯誤記錄:",
                *[f"• {error}" for error in report_data['errors'][:3]]  # 最多顯示3個錯誤
            ])
        
        report_parts.append(f"\n⏰ {datetime.now(self.timezone).strftime('%Y-%m-%d %H:%M:%S')}")
        
        return "\n".join(report_parts)
    
    def get_quota_status(self) -> Dict[str, Any]:
        """獲取 LINE Bot 配額狀態（如果需要的話）"""
        # 這裡可以實作配額追蹤邏輯
        # 由於您提到每月只有 200 則推播，可以在這裡追蹤使用量
        return {
            'monthly_limit': 200,
            'used_this_month': 0,  # 可以從資料庫或檔案讀取
            'remaining': 200
        }
    
    def __del__(self):
        """清理資源"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=False)
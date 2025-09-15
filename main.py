# main.py
# -*- coding: utf-8 -*-
"""
LINE Bot 新聞推播系統 - 優化版 v2.0
"""

import asyncio
import sys
import traceback
from typing import Dict, Optional

from dotenv import load_dotenv

# 匯入所有必要的模組
from utils.config_manager import ConfigManager
from utils.http_client import AsyncHTTPClient
from core import (
    NewsFetcher,
    NewsClassifier,
    NewsProcessor,
    LineNotifier,
    SheetManager
)
from utils.logger import get_logger

# ❗️【核心修正】將 logger 的初始化移回到全域範圍
# 這樣 NewsBot 類別和 main() 函式都可以存取它
logger = get_logger(__name__)

# ==============================================================================
# NewsBot 主類別
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
        try:
            self.http_client = AsyncHTTPClient(self.config)
            self.sheet_manager = SheetManager(self.config)
            self.news_fetcher = NewsFetcher(self.config, self.http_client)
            self.news_classifier = NewsClassifier(self.config, self.http_client)
            self.news_processor = NewsProcessor(self.config, self.http_client, self.sheet_manager)
            self.line_notifier = LineNotifier(self.config)
            logger.info("所有系統組件初始化成功") # 現在可以正確存取 logger
        except Exception as e:
            logger.error(f"系統組件初始化失敗: {e}", exc_info=True) # 現在可以正確存取 logger
            raise

    async def run_pipeline(self) -> None:
        """執行完整的新聞處理流水線"""
        logger.info("=" * 50)
        logger.info(f"🚀 啟動新聞處理流水線 v{self.config.get('app', {}).get('version', 'N/A')}")
        logger.info("=" * 50)
        
        try:
            # 步驟 1: 初始化 G-Sheets 並載入資料
            logger.info("📊 初始化 Google Sheets 連線...")
            await self.sheet_manager.initialize()
            glossary = await self.sheet_manager.load_glossary()
            logger.info(f"📚 載入術語表: {len(glossary)} 條術語")
            sent_links = await self.sheet_manager.get_sent_links()
            logger.info(f"📝 載入已發送記錄: {len(sent_links)} 條")
            
            # 步驟 2: 抓取新聞
            logger.info("📰 開始抓取新聞來源...")
            all_news = await self.news_fetcher.fetch_all_news()
            
            # 步驟 3: 分類與過濾
            logger.info("🎯 開始新聞分類與過濾...")
            important_news = await self.news_classifier.classify_and_filter(all_news, sent_links)
            logger.info(f"✨ 過濾出 {len(important_news)} 則重要新聞")
            
            # 步驟 4: 摘要與處理
            logger.info("📝 開始新聞處理與摘要生成...")
            processed_news = await self.news_processor.process_news(important_news, glossary)
            logger.info(f"🎉 生成 {len(processed_news)} 則處理後新聞")
            
            if not processed_news:
                logger.info("ℹ️ 沒有需要推播的新聞")
                return
            
            # 步驟 5: 推播到 LINE
            logger.info("📱 開始發送 LINE 通知...")
            await self.line_notifier.send_news_batch(processed_news)
            
            # 步驟 6: 記錄到 G-Sheets
            logger.info("💾 記錄發送結果到 Google Sheets...")
            await self.sheet_manager.log_sent_news(processed_news)
            
            logger.info("🎊 新聞流水線執行完成！")
            
        except Exception as e:
            logger.error(f"💥 流水線執行失敗: {e}", exc_info=True)

# ==============================================================================
# 應用程式主入口
# ==============================================================================
async def main():
    """應用程式主執行函式"""
    load_dotenv(verbose=True) 

    config = ConfigManager().load_config()

    logger.info("🔧 偵測到本地執行模式")
    logger.info(f"設定檔 {config['env']} 載入成功 - {config['app']['name']} v{config['app']['version']}")
    
    news_bot = None
    try:
        news_bot = NewsBot(config)
        await news_bot.run_pipeline()
    finally:
        if news_bot and news_bot.http_client:
            logger.info("🔌 正在關閉 HTTP 客戶端連線...")
            await news_bot.http_client.close()
            logger.info("✅ HTTP 客戶端已關閉")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 收到中斷信號，程式正在退出...")
    except Exception as e:
        logger.error(f"💥 程式執行時發生未預期的嚴重錯誤: {e}", exc_info=True)
        sys.exit(1)
# -*- coding: utf-8 -*-
"""
LINE Bot 新聞推播系統 - 優化版 v2.0
主要改進：
1. 模組化架構
2. 指數退避重試機制  
3. 術語表整合
4. 設定檔支援
5. 異步優化與 timeout 預防
6. 更完善的錯誤處理
"""

import asyncio
import logging
import os
import signal
import sys
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import yaml
from dotenv import load_dotenv
from flask import Flask
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from core.config_manager import ConfigManager
from core.news_fetcher import NewsFetcher
from core.news_classifier import OptimizedNewsClassifier
from core.news_processor import NewsProcessor
from core.line_notifier import LineNotifier
from core.sheet_manager import SheetManager
from utils.logger import setup_logger

# ==============================================================================
# 全域設定與初始化
# ==============================================================================

# 載入環境變數
load_dotenv()

# 設定日誌
logger = setup_logger(__name__)

# 載入設定檔
try:
    config_manager = ConfigManager()
    config = config_manager.get_config()
    logger.info(f"設定檔載入成功 - {config['app']['name']} v{config['app']['version']}")
except Exception as e:
    logger.error(f"設定檔載入失敗: {e}")
    sys.exit(1)

# 驗證必要環境變數
required_env_vars = [
    "OPENROUTER_API_KEY", 
    "LINE_CHANNEL_ACCESS_TOKEN", 
    "USER_ID", 
    "GOOGLE_SHEET_URL"
]

missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars and os.getenv("IS_CLOUD_RUN") != "true":
    logger.error(f"缺少必要環境變數: {missing_vars}")
    sys.exit(1)

# ==============================================================================
# 主要業務邏輯類別
# ==============================================================================

class NewsBot:
    """新聞機器人主控制器 - 統籌所有組件"""
    
    def __init__(self):
        self.config = config
        self.execution_start_time = None
        self.max_execution_time = config['cloud_run']['max_execution_time']
        
        # 初始化各個組件
        self._initialize_components()
        
        # 設定優雅關閉處理
        self._setup_signal_handlers()
    
    def _initialize_components(self):
        """初始化所有系統組件"""
        try:
            self.sheet_manager = SheetManager(self.config)
            self.news_fetcher = NewsFetcher(self.config)
            self.news_classifier = OptimizedNewsClassifier(self.config)
            self.news_processor = NewsProcessor(self.config)
            self.line_notifier = LineNotifier(self.config)
            logger.info("所有系統組件初始化成功")
        except Exception as e:
            logger.error(f"系統組件初始化失敗: {e}")
            raise
    
    def _setup_signal_handlers(self):
        """設定信號處理器以優雅關閉"""
        def signal_handler(signum, frame):
            logger.warning(f"收到信號 {signum}，開始優雅關閉...")
            self._graceful_shutdown()
            sys.exit(0)
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    
    def _graceful_shutdown(self):
        """優雅關閉處理"""
        logger.info("正在進行優雅關閉...")
        # 這裡可以加入清理資源的邏輯
        
    def _check_execution_time(self) -> bool:
        """檢查是否接近執行時間上限"""
        if not self.execution_start_time:
            return True
            
        elapsed = time.time() - self.execution_start_time
        remaining = self.max_execution_time - elapsed
        
        if remaining < 60:  # 少於1分鐘時警告
            logger.warning(f"⏰ 執行時間即將到達上限，剩餘 {remaining:.1f} 秒")
            return False
        
        return True
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((Exception,))
    )
    async def run_pipeline(self) -> Dict:
        """執行完整的新聞處理流水線"""
        self.execution_start_time = time.time()
        logger.info("=" * 50)
        logger.info("🚀 啟動新聞處理流水線 v2.0")
        logger.info("=" * 50)
        
        results = {
            'success': False,
            'processed_count': 0,
            'sent_count': 0,
            'execution_time': 0,
            'errors': []
        }
        
        try:
            # 步驟 1: 初始化 Google Sheets 連線與載入術語表
            logger.info("📊 初始化 Google Sheets 連線...")
            if not self._check_execution_time():
                raise TimeoutError("執行時間不足，提前終止")
                
            await self.sheet_manager.initialize()
            glossary = await self.sheet_manager.load_glossary()
            logger.info(f"📚 載入術語表: {len(glossary)} 條術語")
            
            # 步驟 2: 獲取已發送新聞清單
            sent_links = await self.sheet_manager.get_sent_links()
            logger.info(f"📝 載入已發送記錄: {len(sent_links)} 條")
            
            # 步驟 3: 抓取所有新聞來源
            logger.info("📰 開始抓取新聞來源...")
            if not self._check_execution_time():
                raise TimeoutError("執行時間不足，提前終止")
                
            all_news = await self.news_fetcher.fetch_all_news()
            logger.info(f"📊 共抓取 {len(all_news)} 則原始新聞")
            
            # 步驟 4: 分類與過濾重要新聞
            logger.info("🎯 開始新聞分類與過濾...")
            if not self._check_execution_time():
                raise TimeoutError("執行時間不足，提前終止")
                
            important_news = await self.news_classifier.classify_and_filter(
                all_news, sent_links
            )
            logger.info(f"✨ 過濾出 {len(important_news)} 則重要新聞")
            
            # 步驟 5: 新聞處理與摘要生成
            logger.info("📝 開始新聞處理與摘要生成...")
            if not self._check_execution_time():
                raise TimeoutError("執行時間不足，提前終止")
                
            processed_news = await self.news_processor.process_news(
                important_news, glossary
            )
            results['processed_count'] = len(processed_news)
            logger.info(f"🎉 生成 {len(processed_news)} 則處理後新聞")
            
            if not processed_news:
                logger.info("ℹ️ 沒有需要推播的新聞")
                results['success'] = True
                return results
            
            # 步驟 6: 發送到 LINE
            logger.info("📱 開始發送 LINE 通知...")
            if not self._check_execution_time():
                raise TimeoutError("執行時間不足，提前終止")
                
            sent_count = await self.line_notifier.send_news_batch(processed_news)
            results['sent_count'] = sent_count
            
            # 步驟 7: 記錄到 Google Sheets
            logger.info("💾 記錄發送結果到 Google Sheets...")
            await self.sheet_manager.log_sent_news(processed_news)
            
            results['success'] = True
            logger.info("🎊 新聞流水線執行完成！")
            
        except TimeoutError as e:
            logger.error(f"⏰ 執行逾時: {e}")
            results['errors'].append(f"Timeout: {str(e)}")
        except Exception as e:
            logger.error(f"💥 流水線執行失敗: {e}")
            logger.error(traceback.format_exc())
            results['errors'].append(str(e))
        finally:
            results['execution_time'] = time.time() - self.execution_start_time
            logger.info(f"⏱️ 總執行時間: {results['execution_time']:.2f} 秒")
        
        return results

# ==============================================================================
# Flask 應用程式
# ==============================================================================

app = Flask(__name__)
news_bot = NewsBot()

@app.route("/", methods=["GET", "POST"])
def main_handler():
    """主要的 HTTP 處理端點"""
    try:
        # 在新的事件循環中執行異步流水線
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            results = loop.run_until_complete(news_bot.run_pipeline())
            return _format_response(results), 200 if results['success'] else 500
        finally:
            loop.close()
            
    except Exception as e:
        error_msg = f"Critical error in main handler: {str(e)}\n{traceback.format_exc()[:4000]}"
        logger.error(error_msg)
        return error_msg, 500

def _format_response(results: Dict) -> str:
    """格式化回應訊息"""
    status = "✅ SUCCESS" if results['success'] else "❌ FAILED"
    
    response = f"""
{status} - News Pipeline Execution Report
=====================================
• Processed: {results['processed_count']} articles
• Sent: {results['sent_count']} messages  
• Execution Time: {results['execution_time']:.2f}s
• Max Time Limit: {news_bot.max_execution_time}s
"""
    
    if results['errors']:
        response += f"\n• Errors: {len(results['errors'])}\n"
        for error in results['errors']:
            response += f"  - {error}\n"
    
    return response.strip()

@app.route("/health", methods=["GET"])
def health_check():
    """健康檢查端點"""
    return {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': config['app']['version']
    }, 200

# ==============================================================================
# 本地執行入口
# ==============================================================================

if __name__ == "__main__":
    logger.info("🔧 偵測到本地執行模式")
    try:
        # 建立新的事件循環並執行
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            results = loop.run_until_complete(news_bot.run_pipeline())
            print(_format_response(results))
        finally:
            loop.close()
            
    except KeyboardInterrupt:
        logger.info("👋 收到中斷信號，正在退出...")
    except Exception as e:
        logger.error(f"💥 本地執行失敗: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)
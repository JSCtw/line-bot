# -*- coding: utf-8 -*-
"""
異步新聞抓取器
整合 RSS 和 HTML 新聞來源的抓取，具備並發控制和錯誤處理
"""

import asyncio
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import feedparser
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.logger import log_async_execution_time

logger = logging.getLogger(__name__)

class NewsFetcher:
    """異步新聞抓取器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.http_config = config.get('http', {})
        
        # HTTP 設定
        self.timeout = self.http_config.get('timeout', 15)
        self.user_agent = self.http_config.get('user_agent', 'Mozilla/5.0 (compatible; NewsBot/2.0)')
        self.max_concurrent = self.http_config.get('max_concurrent', 10)
        
        # 新聞來源設定
        self.rss_feeds = config.get('news_sources', {}).get('rss_feeds', {})
        self.html_sources = config.get('news_sources', {}).get('html_sources', {})
        
        # 處理設定
        self.processing_config = config.get('news_processing', {})
        self.max_news_per_source = self.processing_config.get('max_news_per_source', 20)
        self.min_content_length = self.processing_config.get('min_content_length', 50)
        self.max_content_preview = self.processing_config.get('max_content_preview', 2000)
        
        # 線程池用於同步操作
        self.executor = ThreadPoolExecutor(max_workers=5)
    
    @log_async_execution_time()
    async def fetch_all_news(self) -> List[Dict[str, Any]]:
        """抓取所有新聞來源"""
        logger.info("🔄 開始抓取所有新聞來源...")
        
        all_news = []
        
        # 創建連線器限制並發數
        connector = aiohttp.TCPConnector(limit=self.max_concurrent, limit_per_host=5)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={'User-Agent': self.user_agent}
        ) as session:
            
            # 並發抓取 RSS 和 HTML 來源
            tasks = []
            
            # RSS 來源任務
            for source_name, rss_url in self.rss_feeds.items():
                task = self._fetch_rss_news(session, source_name, rss_url)
                tasks.append(task)
            
            # HTML 來源任務
            for source_name, source_config in self.html_sources.items():
                task = self._fetch_html_news(session, source_name, source_config)
                tasks.append(task)
            
            # 執行所有任務
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 收集成功的結果
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"❌ 第 {i+1} 個來源抓取失敗: {result}")
                elif isinstance(result, list):
                    all_news.extend(result)
                    logger.info(f"✅ 第 {i+1} 個來源抓取成功: {len(result)} 則新聞")
        
        logger.info(f"📊 總共抓取 {len(all_news)} 則新聞")
        return all_news
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((Exception,))
    )
    async def _fetch_rss_news(self, session: aiohttp.ClientSession, source_name: str, rss_url: str) -> List[Dict]:
        """抓取 RSS 新聞來源"""
        logger.info(f"📡 抓取 RSS: {source_name}")
        
        try:
            # 在線程池中執行同步的 feedparser 操作
            feed = await asyncio.get_event_loop().run_in_executor(
                self.executor, feedparser.parse, rss_url
            )
            
            news_items = []
            entries = feed.entries[:self.max_news_per_source]
            
            for entry in entries:
                if not (hasattr(entry, "title") and entry.title and 
                       hasattr(entry, "link") and entry.link):
                    continue
                
                # 取得內容
                content = ""
                if hasattr(entry, "summary"):
                    soup = BeautifulSoup(entry.summary, "lxml")
                    content = soup.get_text(separator="\n", strip=True)
                
                # 過濾太短的內容
                if len(content) < self.min_content_length:
                    continue
                
                news_item = {
                    "source": source_name,
                    "title": entry.title.strip(),
                    "link": entry.link.strip(),
                    "content": content[:self.max_content_preview],
                    "published": getattr(entry, 'published', ''),
                    "type": "rss"
                }
                
                news_items.append(news_item)
            
            logger.info(f"✅ RSS {source_name}: {len(news_items)} 則新聞")
            return news_items
            
        except Exception as e:
            logger.error(f"❌ RSS {source_name} 抓取失敗: {e}")
            raise
    
    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((Exception,))
    )
    async def _fetch_html_news(self, session: aiohttp.ClientSession, source_name: str, source_config: Dict) -> List[Dict]:
        """抓取 HTML 新聞來源"""
        logger.info(f"🌐 抓取 HTML: {source_name}")
        
        try:
            base_url = source_config['url']
            max_articles = source_config.get('max_articles', 15)
            
            # 抓取主頁面
            async with session.get(base_url) as response:
                response.raise_for_status()
                html_content = await response.text()
            
            soup = BeautifulSoup(html_content, "html.parser")
            articles = soup.select(source_config['selector'])[:max_articles]
            
            logger.info(f"🔍 {source_name} 找到 {len(articles)} 篇文章")
            
            # 並發抓取文章內容
            content_tasks = []
            for article in articles:
                task = self._extract_article_content(session, source_name, article, source_config)
                content_tasks.append(task)
            
            # 限制並發數避免過載
            news_items = []
            for i in range(0, len(content_tasks), 5):  # 每次處理5個
                batch = content_tasks[i:i+5]
                batch_results = await asyncio.gather(*batch, return_exceptions=True)
                
                for result in batch_results:
                    if isinstance(result, dict):
                        news_items.append(result)
                    elif isinstance(result, Exception):
                        logger.warning(f"⚠️ 文章內容抓取失敗: {result}")
                
                # 批次間稍作延遲
                if i + 5 < len(content_tasks):
                    await asyncio.sleep(0.5)
            
            logger.info(f"✅ HTML {source_name}: {len(news_items)} 則新聞")
            return news_items
            
        except Exception as e:
            logger.error(f"❌ HTML {source_name} 抓取失敗: {e}")
            raise
    
    async def _extract_article_content(self, session: aiohttp.ClientSession, source_name: str, 
                                     article, source_config: Dict) -> Dict:
        """從文章元素提取內容"""
        try:
            # 提取標題
            title_elem = article.select_one(source_config['title_selector'])
            if not title_elem:
                raise ValueError("找不到標題元素")
            
            title = title_elem.get_text(strip=True)
            
            # 提取連結
            link_elem = article.select_one(source_config['link_selector'])
            if not link_elem:
                raise ValueError("找不到連結元素")
            
            link = link_elem.get("href", "")
            if link and not link.startswith("http"):
                # 處理相對連結
                base_domain = source_config['url'].rstrip('/')
                if link.startswith('/'):
                    link = base_domain + link
                else:
                    link = base_domain + '/' + link
            
            # 抓取文章詳細內容
            content = await self._fetch_article_detail(session, link, source_config)
            
            # 驗證內容長度
            if len(content) < self.min_content_length:
                raise ValueError(f"內容太短: {len(content)} < {self.min_content_length}")
            
            return {
                "source": source_name,
                "title": title,
                "link": link,
                "content": content[:self.max_content_preview],
                "type": "html"
            }
            
        except Exception as e:
            logger.debug(f"文章提取失敗: {e}")
            raise
    
    async def _fetch_article_detail(self, session: aiohttp.ClientSession, url: str, source_config: Dict) -> str:
        """抓取文章詳細內容"""
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                html_content = await response.text()
            
            soup = BeautifulSoup(html_content, "html.parser")
            content_elem = soup.select_one(source_config['content_selector'])
            
            if content_elem:
                content = content_elem.get_text(separator="\n", strip=True)
                return content
            else:
                return ""
                
        except Exception as e:
            logger.debug(f"文章內容抓取失敗 {url}: {e}")
            return ""
    
    def __del__(self):
        """清理資源"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=False)
# core/news_fetcher.py
# -*- coding: utf-8 -*-
"""
異步新聞抓取器
整合 RSS 和 HTML 新聞來源的抓取，具備並發控制和錯誤處理
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.http_client import AsyncHTTPClient
from utils.logger import get_logger, log_async_execution_time

logger = get_logger(__name__)

class NewsFetcher:
    """異步新聞抓取器"""
    
    def __init__(self, config: Dict[str, Any], http_client: AsyncHTTPClient):
        self.config = config
        # ❗️【核心修改】統一使用傳入的 http_client
        self.http_client = http_client
        
        # 新聞來源設定
        self.news_sources_config = config.get('news_sources', {})
        self.rss_feeds = self.news_sources_config.get('rss_feeds', {})
        self.html_sources = self.news_sources_config.get('html_sources', {})
        
        # 處理設定
        self.processing_config = config.get('news_processing', {})
        self.max_news_per_source = self.processing_config.get('max_news_per_source', 20)
        self.min_content_length = self.processing_config.get('min_content_length', 50)
        self.max_content_preview = self.processing_config.get('max_content_preview', 2000)
    
    @log_async_execution_time("fetch_all_news")
    async def fetch_all_news(self) -> List[Dict[str, Any]]:
        """抓取所有新聞來源"""
        logger.info("🔄 開始抓取所有新聞來源...")
        
        tasks = []
        # RSS 來源任務
        for source_name, rss_url in self.rss_feeds.items():
            tasks.append(self._fetch_rss_news(source_name, rss_url))
        
        # HTML 來源任務
        for source_name, source_config in self.html_sources.items():
            tasks.append(self._fetch_html_news(source_name, source_config))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_news = []
        for i, result in enumerate(results):
            if isinstance(result, list):
                all_news.extend(result)
                logger.info(f"✅ 第 {i+1} 個來源抓取成功: {len(result)} 則新聞")
            elif isinstance(result, Exception):
                logger.error(f"❌ 第 {i+1} 個來源抓取失敗: {result}", exc_info=False) # exc_info=False 避免洗版
        
        logger.info(f"📊 總共抓取 {len(all_news)} 則原始新聞")
        return all_news
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception)
    )
    async def _fetch_rss_news(self, source_name: str, rss_url: str) -> List[Dict]:
        """抓取 RSS 新聞來源"""
        logger.info(f"📡 抓取 RSS: {source_name}")
        try:
            # ❗️【核心修改】直接使用 http_client 獲取文本內容
            response_text = await self.http_client.get_text(rss_url)
            feed = feedparser.parse(response_text)
            
            news_items = []
            for entry in feed.entries[:self.max_news_per_source]:
                title = getattr(entry, 'title', '').strip()
                link = getattr(entry, 'link', '').strip()

                # ❗️【防禦性檢查】確保新聞有標題和連結
                if not title or not link:
                    logger.warning(f"⚠️ 發現一則無標題或無連結的 RSS 新聞，已跳過。來源: {source_name}")
                    continue
                
                content_soup = BeautifulSoup(getattr(entry, 'summary', ''), "lxml")
                content = content_soup.get_text(separator="\n", strip=True)
                
                if len(content) < self.min_content_length:
                    continue
                
                news_items.append({
                    "source": source_name,
                    "title": title,
                    "link": link,
                    "content": content[:self.max_content_preview],
                    "published": getattr(entry, 'published', ''),
                    "type": "rss"
                })
            
            logger.info(f"✅ RSS {source_name}: 抓取到 {len(news_items)} 則有效新聞")
            return news_items
        except Exception as e:
            logger.error(f"❌ RSS {source_name} 抓取失敗: {e}")
            raise

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type(Exception)
    )
    async def _fetch_html_news(self, source_name: str, source_config: Dict) -> List[Dict]:
        """抓取 HTML 新聞來源"""
        logger.info(f"🌐 抓取 HTML: {source_name}")
        try:
            base_url = source_config['url']
            html_content = await self.http_client.get_text(base_url)
            
            soup = BeautifulSoup(html_content, "html.parser")
            article_elements = soup.select(source_config['selector'])[:source_config.get('max_articles', 15)]
            
            logger.info(f"🔍 {source_name} 在首頁找到 {len(article_elements)} 篇文章連結")
            
            content_tasks = [
                self._extract_article_content(source_name, article_elem, source_config)
                for article_elem in article_elements
            ]
            
            results = await asyncio.gather(*content_tasks, return_exceptions=True)
            
            # 過濾掉失敗的結果 (None)
            news_items = [item for item in results if isinstance(item, dict)]
            
            logger.info(f"✅ HTML {source_name}: 成功解析 {len(news_items)} 則新聞")
            return news_items
        except Exception as e:
            logger.error(f"❌ HTML {source_name} 抓取失敗: {e}")
            raise
    
    async def _extract_article_content(self, source_name: str, article_elem, source_config: Dict) -> Optional[Dict]:
        """從文章元素提取標題、連結和內容，失敗時返回 None"""
        try:
            title_elem = article_elem.select_one(source_config['title_selector'])
            link_elem = article_elem.select_one(source_config['link_selector'])

            if not title_elem or not link_elem:
                return None

            title = title_elem.get_text(strip=True)
            relative_link = link_elem.get("href", "")
            
            # ❗️【防禦性檢查】確保提取到了標題和連結
            if not title or not relative_link:
                return None
            
            # 處理相對路徑，使其變為絕對路徑
            absolute_link = urljoin(source_config['url'], relative_link)
            
            content = await self._fetch_article_detail(absolute_link, source_config)
            
            if len(content) < self.min_content_length:
                return None
            
            return {
                "source": source_name,
                "title": title,
                "link": absolute_link,
                "content": content[:self.max_content_preview],
                "type": "html"
            }
        except Exception as e:
            logger.debug(f"HTML 文章內容提取失敗: {e}") # 使用 debug 等級避免洗版
            return None
    
    async def _fetch_article_detail(self, url: str, source_config: Dict) -> str:
        """抓取文章詳細頁面並提取主要內容"""
        try:
            html_content = await self.http_client.get_text(url)
            soup = BeautifulSoup(html_content, "html.parser")
            content_elem = soup.select_one(source_config['content_selector'])
            
            return content_elem.get_text(separator="\n", strip=True) if content_elem else ""
        except Exception as e:
            logger.debug(f"文章詳細內容抓取失敗 {url}: {e}")
            return ""

    # ❗️【移除】不再需要 __del__ 和 ThreadPoolExecutor，因為我們全面使用異步 http_client
    # def __del__(self):
    #     if hasattr(self, 'executor'):
    #         self.executor.shutdown(wait=False)
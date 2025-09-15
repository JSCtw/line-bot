# core/news_fetcher.py

import asyncio
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
# ❗️【步驟一】重新匯入 ThreadPoolExecutor
from concurrent.futures import ThreadPoolExecutor

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
        self.http_client = http_client
        
        self.news_sources_config = config.get('news_sources', {})
        self.rss_feeds = self.news_sources_config.get('rss_feeds') or {}
        self.html_sources = self.news_sources_config.get('html_sources') or {}
        
        self.processing_config = config.get('news_processing', {})
        self.max_news_per_source = self.processing_config.get('max_news_per_source', 20)
        self.min_content_length = self.processing_config.get('min_content_length', 50)
        self.max_content_preview = self.processing_config.get('max_content_preview', 2000)

        # ❗️【步驟二】重新建立線程池執行器
        self.executor = ThreadPoolExecutor(max_workers=5)
    
    # ... (fetch_all_news 方法保持不變) ...
    @log_async_execution_time("fetch_all_news")
    async def fetch_all_news(self) -> List[Dict[str, Any]]:
        logger.info("🔄 開始抓取所有新聞來源...")
        tasks = []
        for source_name, rss_url in self.rss_feeds.items():
            tasks.append(asyncio.create_task(self._fetch_rss_news(source_name, rss_url)))
        for source_name, source_config in self.html_sources.items():
            tasks.append(asyncio.create_task(self._fetch_html_news(source_name, source_config)))
        if not tasks:
            logger.warning("⚠️ 設定檔中未找到任何 RSS 或 HTML 新聞來源")
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_news = []
        for i, result in enumerate(results):
            if isinstance(result, list):
                all_news.extend(result)
                logger.info(f"✅ 第 {i+1} 個來源抓取成功: {len(result)} 則新聞")
            elif isinstance(result, Exception):
                logger.error(f"❌ 第 {i+1} 個來源抓取失敗: {result}", exc_info=False)
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
            # 我們仍然用異步方式獲取文本
            response_text = await self.http_client.get_text(rss_url)

            # ❗️【步驟三】將同步的 feedparser 操作放入線程池中執行
            loop = asyncio.get_running_loop()
            feed = await loop.run_in_executor(
                self.executor, feedparser.parse, response_text
            )
            
            # ... 後續的解析邏輯完全不變 ...
            news_items = []
            for entry in feed.entries[:self.max_news_per_source]:
                title = getattr(entry, 'title', '').strip()
                link = getattr(entry, 'link', '').strip()
                if not title or not link:
                    logger.warning(f"⚠️ 發現一則無標題或無連結的 RSS 新聞，已跳過。來源: {source_name}")
                    continue
                content_soup = BeautifulSoup(getattr(entry, 'summary', ''), "lxml")
                content = content_soup.get_text(separator="\n", strip=True)
                if len(content) < self.min_content_length:
                    continue
                news_items.append({
                    "source": source_name, "title": title, "link": link,
                    "content": content[:self.max_content_preview],
                    "published": getattr(entry, 'published', ''), "type": "rss"
                })
            
            logger.info(f"✅ RSS {source_name}: 抓取到 {len(news_items)} 則有效新聞")
            return news_items
        except Exception as e:
            logger.error(f"❌ RSS {source_name} 抓取失敗: {e}")
            raise

    # ... (所有 _fetch_html_news 相關的方法都保持不變) ...
    @retry( stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8), retry=retry_if_exception_type(Exception) )
    async def _fetch_html_news(self, source_name: str, source_config: Dict) -> List[Dict]:
        logger.info(f"🌐 抓取 HTML: {source_name}")
        try:
            base_url = source_config['url']
            html_content = await self.http_client.get_text(base_url)
            soup = BeautifulSoup(html_content, "html.parser")
            article_elements = soup.select(source_config['selector'])[:source_config.get('max_articles', 15)]
            logger.info(f"🔍 {source_name} 在首頁找到 {len(article_elements)} 篇文章連結")
            content_tasks = [ self._extract_article_content(source_name, article_elem, source_config) for article_elem in article_elements ]
            results = await asyncio.gather(*content_tasks)
            news_items = [item for item in results if item is not None]
            logger.info(f"✅ HTML {source_name}: 成功解析 {len(news_items)} 則新聞")
            return news_items
        except Exception as e:
            logger.error(f"❌ HTML {source_name} 抓取失敗: {e}")
            raise
    
    async def _extract_article_content(self, source_name: str, article_elem, source_config: Dict) -> Optional[Dict]:
        try:
            title_elem = article_elem.select_one(source_config['title_selector'])
            link_elem = article_elem.select_one(source_config['link_selector'])
            if not title_elem or not link_elem: return None
            title = title_elem.get_text(strip=True)
            relative_link = link_elem.get("href", "")
            if not title or not relative_link: return None
            absolute_link = urljoin(source_config['url'], relative_link)
            content = await self._fetch_article_detail(absolute_link, source_config)
            if len(content) < self.min_content_length: return None
            return { "source": source_name, "title": title, "link": absolute_link, "content": content[:self.max_content_preview], "type": "html" }
        except Exception as e:
            logger.debug(f"HTML 文章內容提取失敗: {e}")
            return None
    
    async def _fetch_article_detail(self, url: str, source_config: Dict) -> str:
        try:
            html_content = await self.http_client.get_text(url)
            soup = BeautifulSoup(html_content, "html.parser")
            content_elem = soup.select_one(source_config['content_selector'])
            return content_elem.get_text(separator="\n", strip=True) if content_elem else ""
        except Exception as e:
            logger.debug(f"文章詳細內容抓取失敗 {url}: {e}")
            return ""
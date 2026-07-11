# core/news_classifier.py (v3.3 修正)
# -*- coding: utf-8 -*-
"""
優化的新聞分類器
使用異步批次分類，具備並發控制、錯誤處理和重試機制
"""

import asyncio
import logging
import re
import time
from typing import List, Dict, Any, Set

# 移除了 'difflib' 和 'ThreadPoolExecutor'，它們不屬於此檔案
from utils.http_client import AsyncHTTPClient
from utils.logger import log_async_execution_time, get_logger

# 使用 get_logger 獲取與我們設定一致的 logger
logger = get_logger(__name__)

class OptimizedNewsClassifier:
    """優化的異步新聞分類器"""
    
    # [v3.3 優化] 將 Regex 編譯為類別常數，避免在每次解析時重複編譯
    BATCH_PARSE_PATTERN = re.compile(
        r"Article\s+(\d+):\s*Topic:\s*(.*?)\s*\|\s*Scope:\s*(.*?)(?:\n|$)", 
        re.IGNORECASE
    )
    
    def __init__(self, config: Dict[str, Any], http_client: AsyncHTTPClient):
        self.config = config
        self.http_client = http_client
        
        # 讀取分類模型的設定
        self.ai_config = self.config.get("ai_models", {}).get("classification", {})
        
        # 分類器處理參數
        self.classifier_config = config.get('classifier', {})
        self.max_concurrent = self.classifier_config.get('max_concurrent', 5)
        self.batch_size = self.classifier_config.get('batch_size', 8)
        
        # 分類關鍵詞
        self.scope_keywords = self.classifier_config.get('scope_keywords', {})
        
        # (移除了不必要的 self.executor)
        
    @log_async_execution_time("classify_and_filter_news")
    async def classify_and_filter(self, news_list: List[Dict], sent_links: Set[str]) -> List[Dict]:
        """
        分類並過濾新聞
        """
        # 過濾未發送的新聞
        unsent_news = [
            news for news in news_list 
            if news.get("link") not in sent_links
        ]
        
        if not unsent_news:
            logger.info("ℹ️ 沒有未發送的新聞需要分類")
            return []
        
        logger.info(f"🎯 開始分類 {len(unsent_news)} 則未發送新聞...")

        # 執行批次分類
        classified_results = await self._classify_news_batch(unsent_news)

        # [v3.7.1] 全滅防護：若所有批次都分類失敗 (如模型下架、配額耗盡)，
        # 應視為流水線錯誤並中止，而不是默默回傳 0 則讓用戶收到「沒有新聞」的誤導訊息
        if classified_results and all(
            r.get("scope_raw") == "分類失敗" for r in classified_results
        ):
            raise RuntimeError(
                f"AI 分類全面失敗 ({len(classified_results)} 則全數失敗)，"
                f"可能原因: 模型限流/下架或 API 配額耗盡，已中止流水線"
            )

        # 過濾重要新聞
        important_news = self._filter_important_news(classified_results)
        
        logger.info(f"✨ 過濾出 {len(important_news)} 則重要新聞")
        return important_news
    
    @log_async_execution_time("classify_all_batches")
    async def _classify_news_batch(self, news_list: List[Dict]) -> List[Dict]:
        """批次分類新聞"""
        logger.info(f"📦 開始批次分類，共 {len(news_list)} 則新聞")
        
        batches = [
            news_list[i:i + self.batch_size] 
            for i in range(0, len(news_list), self.batch_size)
        ]
        
        logger.info(f"🔄 分為 {len(batches)} 批，每批最多 {self.batch_size} 則")
        
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def run_with_semaphore(task):
            async with semaphore:
                return await task

        tasks = [
            run_with_semaphore(self._classify_single_batch(i, batch)) 
            for i, batch in enumerate(batches)
        ]
        
        batch_results_list = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_results = []
        for i, result in enumerate(batch_results_list):
            if isinstance(result, Exception):
                logger.error(f"❌ 第 {i+1} 批分類時發生無法恢復的錯誤: {result}")
                failed_batch = batches[i]
                default_results = [
                    {"news": news, "topic": "未知", "scope_raw": "分類失敗"} 
                    for news in failed_batch
                ]
                all_results.extend(default_results)
            elif isinstance(result, list):
                all_results.extend(result)
        
        return all_results
    
    async def _classify_single_batch(self, batch_index: int, batch: List[Dict]) -> List[Dict]:
        """分類單一批次"""
        logger.info(f"🔄 處理第 {batch_index + 1} 批，共 {len(batch)} 則新聞")
        
        try:
            prompt = self._create_batch_classification_prompt(batch)
            
            response_text = await self.http_client.call_ai_api(
                prompt=prompt,
                **self.ai_config
            )
            
            parsed_results = self._parse_batch_response(response_text, batch)
            
            logger.info(f"✅ 第 {batch_index + 1} 批分類完成")
            return parsed_results
            
        except Exception as e:
            logger.error(f"❌ 在處理第 {batch_index + 1} 批時捕獲到異常: {e}", exc_info=True)
            raise

    def _create_batch_classification_prompt(self, news_batch: List[Dict]) -> str:
        """建立批次分類的 AI 提示"""
        
        input_articles = []
        for i, news_item in enumerate(news_batch, 1):
            title = news_item.get('title', 'No Title')
            content_snippet = news_item.get('content', '')[:500]
            
            input_articles.append(
                f"---\nArticle {i}:\nTitle: {title}\nContent: {content_snippet}\n"
            )
        
        input_articles_str = "\n".join(input_articles)
        
        prompt = f"""You are a news classification expert. Your task is to classify each of the following {len(news_batch)} articles by Topic and Scope.

For each article, you MUST provide its classification on a new line using the EXACT format: 
"Article [index]: Topic: [Topic] | Scope: [Scope]"

**Classification Guidelines:**

TOPICS (choose ONE that best fits):
- 政治 & 外交: Government, politics, elections, diplomacy, international relations
- 經濟 & 金融: Markets, business, trade, economics, financial news
- 軍事 & 安全: Military, defense, conflicts, terrorism, security issues
- 社會 & 人文: Social issues, culture, human rights, society, crime
- 科技 & 創新: Technology, innovation, research, digital trends
- 環境 & 氣候: Climate, environment, natural disasters, sustainability
- 體育 & 娛樂: Sports, entertainment, celebrities, cultural events
- 健康 & 醫療: Healthcare, medical research, public health, diseases

SCOPE (choose ONE):
- 全球性: Affects multiple countries/regions, worldwide impact
- 區域性: Specific to a region, country, or area outside Taiwan
- 國內性: Primarily domestic/local news

**Examples:**
Article 1: Topic: 經濟 & 金融 | Scope: 全球性
Article 2: Topic: 軍事 & 安全 | Scope: 區域性

**Articles to classify:**
{input_articles_str.strip()}

**Your classifications:**"""
        
        return prompt

    def _parse_batch_response(self, response_text: str, news_batch: List[Dict]) -> List[Dict]:
        """解析 AI 批次分類回應"""
        results = []
        
        # [v3.3 優化] 使用預先編譯的類別常數
        
        parsed_indices = set()
        
        for match in self.BATCH_PARSE_PATTERN.finditer(response_text):
            try:
                article_index = int(match.group(1)) - 1
                topic = match.group(2).strip()
                scope_raw = match.group(3).strip()
                
                if 0 <= article_index < len(news_batch):
                    news_item = news_batch[article_index]
                    results.append({
                        "news": news_item,
                        "topic": topic,
                        "scope_raw": scope_raw
                    })
                    parsed_indices.add(article_index)
                    
            except (ValueError, IndexError) as e:
                logger.warning(f"⚠️ 解析分類結果時出錯: {e}, 匹配內容: {match.group(0)}")
        
        # 處理未被解析的新聞項目
        if len(parsed_indices) != len(news_batch):
            for i, news in enumerate(news_batch):
                if i not in parsed_indices:
                    logger.warning(f"⚠️ 第 {i+1} 則新聞未被 AI 正確分類: {news.get('title', 'Unknown Title')}")
                    results.append({
                        "news": news,
                        "topic": "未知",
                        "scope_raw": "未解析"
                    })
        
        return results

    def _filter_important_news(self, classified_results: List[Dict]) -> List[Dict]:
        """根據分類結果過濾重要新聞"""
        
        scope_counts = {"全球性": 0, "區域性": 0, "國內性": 0, "未知": 0, "分類失敗": 0, "未解析": 0}
        filtered_news = []
        
        global_keywords = self.scope_keywords.get('global', ['global', '全球性', '國際性'])
        regional_keywords = self.scope_keywords.get('regional', ['regional', '區域性', '局部性', '國外', '地區', '區域'])
        domestic_keywords = self.scope_keywords.get('domestic', ['domestic', '國內性'])
        
        for result in classified_results:
            scope_raw = result.get('scope_raw', '').lower()
            scope_original = result.get('scope_raw', '')
            
            scope = "未知"
            if "分類失敗" in scope_original:
                scope = "分類失敗"
            elif "未解析" in scope_original:
                scope = "未解析"
            elif any(keyword.lower() in scope_raw for keyword in global_keywords):
                scope = "全球性"
            elif any(keyword.lower() in scope_raw for keyword in regional_keywords):
                scope = "區域性"
            elif any(keyword.lower() in scope_raw for keyword in domestic_keywords):
                scope = "國內性"
            
            scope_counts[scope] += 1
            
            if scope in ['全球性', '區域性']:
                news_item = result["news"].copy()
                news_item['classified_topic'] = result.get('topic', '未知')
                news_item['classified_scope'] = scope
                filtered_news.append(news_item)
        
        logger.info(f"📊 分類統計: {scope_counts}")
        
        return filtered_news
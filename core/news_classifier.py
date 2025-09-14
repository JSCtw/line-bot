# -*- coding: utf-8 -*-
"""
優化的新聞分類器
使用異步批次分類，具備並發控制、錯誤處理和重試機制
"""

import asyncio
import logging
import os
import re
import time
from typing import List, Dict, Any, Set
from concurrent.futures import ThreadPoolExecutor

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# 假設你已將 http_client 匯入到 utils/__init__.py
from utils.http_client import AsyncHTTPClient
from utils.logger import log_async_execution_time

logger = logging.getLogger(__name__)

class OptimizedNewsClassifier:
    """優化的異步新聞分類器"""
    
    def __init__(self, config: Dict[str, Any], http_client: AsyncHTTPClient):
        self.config = config
        self.http_client = http_client
        
        # 讀取分類模型的設定
        self.ai_config = self.config.get("ai_models", {}).get("classification", {})
        
        # 分類器參數
        self.classifier_config = config.get('classifier', {})
        self.max_concurrent = self.classifier_config.get('max_concurrent', 5)
        self.batch_size = self.classifier_config.get('batch_size', 8)
        
        # 分類關鍵詞
        self.scope_keywords = self.classifier_config.get('scope_keywords', {})
        
    @log_async_execution_time()
    async def classify_and_filter(self, news_list: List[Dict], sent_links: Set[str]) -> List[Dict]:
        """
        分類並過濾新聞
        
        Args:
            news_list: 原始新聞列表
            sent_links: 已發送新聞連結集合
            
        Returns:
            過濾後的重要新聞列表
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
        
        # 過濾重要新聞
        important_news = self._filter_important_news(classified_results)
        
        logger.info(f"✨ 過濾出 {len(important_news)} 則重要新聞")
        return important_news
    
    async def _classify_news_batch(self, news_list: List[Dict]) -> List[Dict]:
        """批次分類新聞"""
        logger.info(f"📦 開始批次分類，共 {len(news_list)} 則新聞")
        start_time = time.time()
        
        # 分割成批次
        batches = [
            news_list[i:i + self.batch_size] 
            for i in range(0, len(news_list), self.batch_size)
        ]
        
        logger.info(f"🔄 分為 {len(batches)} 批，每批最多 {self.batch_size} 則")
        
        all_results = []
        
        # 創建任務
        tasks = [
            self._classify_single_batch(i, batch) 
            for i, batch in enumerate(batches)
        ]
        
        # 使用 aiohttp 的並發限制
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def _run_with_semaphore(task):
            async with semaphore:
                return await task

        batch_results_list = await asyncio.gather(*(_run_with_semaphore(task) for task in tasks), return_exceptions=True)
        
        # 處理結果
        for i, result in enumerate(batch_results_list):
            if isinstance(result, Exception):
                logger.error(f"❌ 第 {i+1} 批分類失敗: {result}")
                failed_batch = batches[i]
                default_results = [
                    {"news": news, "topic": "未知", "scope_raw": "未知"} 
                    for news in failed_batch
                ]
                all_results.extend(default_results)
            elif isinstance(result, list):
                all_results.extend(result)
        
        end_time = time.time()
        logger.info(f"🎯 批次分類完成！耗時 {end_time - start_time:.2f} 秒")
        
        return all_results
    
    async def _classify_single_batch(self, batch_index: int, batch: List[Dict]) -> List[Dict]:
        """分類單一批次"""
        logger.info(f"🔄 處理第 {batch_index + 1} 批，共 {len(batch)} 則新聞")
        
        try:
            # 建立分類提示
            prompt = self._create_batch_classification_prompt(batch)
            
            # 呼叫 http_client 的 call_ai_api，將所有 AI 參數傳入
            # `self.ai_config` 包含了 model, temperature, max_tokens 等參數
            response_text = await self.http_client.call_ai_api(
                prompt=prompt,
                **self.ai_config
            )
            
            # 解析回應
            parsed_results = self._parse_batch_response(response_text, batch)
            
            logger.info(f"✅ 第 {batch_index + 1} 批分類完成")
            return parsed_results
            
        except Exception as e:
            logger.error(f"❌ 第 {batch_index + 1} 批分類失敗: {e}")
            raise
    
    def _create_batch_classification_prompt(self, news_batch: List[Dict]) -> str:
        """建立批次分類的 AI 提示"""
        
        # 準備新聞內容
        input_articles = []
        for i, news_item in enumerate(news_batch, 1):
            title = news_item.get('title', '')
            content_snippet = news_item.get('content', '')[:500]  # 限制內容長度
            
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
        
        # 使用正規表達式匹配分類結果
        pattern = re.compile(r"Article\s+(\d+):\s*Topic:\s*(.*?)\s*\|\s*Scope:\s*(.*?)(?:\n|$)", re.IGNORECASE)
        
        parsed_indices = set()
        
        for match in pattern.finditer(response_text):
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
        for i, news in enumerate(news_batch):
            if i not in parsed_indices:
                logger.warning(f"⚠️ 第 {i+1} 則新聞未被正確分類: {news.get('title', 'Unknown Title')}")
                results.append({
                    "news": news,
                    "topic": "未知",
                    "scope_raw": "未知"
                })
        
        return results
    
    def _filter_important_news(self, classified_results: List[Dict]) -> List[Dict]:
        """根據分類結果過濾重要新聞"""
        
        # 統計各分類的數量
        scope_counts = {"全球性": 0, "區域性": 0, "國內性": 0, "未知": 0}
        filtered_news = []
        
        # 獲取關鍵詞配置
        global_keywords = self.scope_keywords.get('global', ['global', '全球性', '國際性'])
        regional_keywords = self.scope_keywords.get('regional', ['regional', '區域性', '局部性', '國外', '地區', '區域'])
        domestic_keywords = self.scope_keywords.get('domestic', ['domestic', '國內性'])
        
        for result in classified_results:
            scope_raw = result.get('scope_raw', '').lower()
            scope_original = result.get('scope_raw', '')
            
            # 判斷範圍類別
            scope = "未知"
            
            # 檢查全球性關鍵詞
            if any(keyword.lower() in scope_raw or keyword in scope_original for keyword in global_keywords):
                scope = "全球性"
            # 檢查區域性關鍵詞
            elif any(keyword.lower() in scope_raw or keyword in scope_original for keyword in regional_keywords):
                scope = "區域性"
            # 檢查國內性關鍵詞
            elif any(keyword.lower() in scope_raw or keyword in scope_original for keyword in domestic_keywords):
                scope = "國內性"
            
            # 更新統計
            scope_counts[scope] += 1
            
            # 過濾重要新聞（全球性和區域性）
            if scope in ['全球性', '區域性']:
                news_item = result["news"].copy()
                news_item['classified_topic'] = result.get('topic', '未知')
                news_item['classified_scope'] = scope
                filtered_news.append(news_item)
        
        # 記錄分類統計
        logger.info(f"📊 分類統計: {scope_counts}")
        
        return filtered_news
    
    async def classify_single_news(self, news_item: Dict) -> Dict:
        """分類單則新聞（用於測試）"""
        try:
            result = await self._classify_single_batch(0, [news_item])
            return result[0] if result else {"news": news_item, "topic": "未知", "scope_raw": "未知"}
            
        except Exception as e:
            logger.error(f"❌ 單則新聞分類失敗: {e}")
            return {"news": news_item, "topic": "未知", "scope_raw": "未知"}
    
    def get_classification_stats(self) -> Dict[str, Any]:
        """獲取分類器統計資訊"""
        return {
            'model_name': self.ai_config.get('name'),
            'max_concurrent': self.max_concurrent,
            'batch_size': self.batch_size,
            'max_retries': self.ai_config.get('max_retries', 3), # 注意: 這裡需要確保 config 裡有這項
            'temperature': self.ai_config.get('temperature')
        }
    
    def __del__(self):
        """清理資源"""
        # 注意: 這邊的 executor 已經不再使用，因為改用 http_client
        # 這裡可以移除，或保留以防未來需要
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=False)
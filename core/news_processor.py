# -*- coding: utf-8 -*-
"""
新聞處理器
負責新聞分組、排序、AI摘要生成與後處理，包含術語表整合
"""

import asyncio
import logging
import random
import re
import time
from typing import Dict, List, Any, Optional
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

class NewsProcessor:
    """新聞處理器 - 負責摘要生成與後處理"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.processing_config = config.get('news_processing', {})
        self.ai_config = config.get('ai_models', {}).get('summarization', {})
        
        # 初始化 OpenAI 客戶端
        self._initialize_openai_client()
        
        # 處理參數
        self.max_groups_to_process = self.processing_config.get('max_groups_to_process', 5)
        self.max_news_per_source = self.processing_config.get('max_news_per_source_in_final', 2)
        self.similarity_threshold = self.processing_config.get('similarity_threshold', 0.7)
        
        # 線程池用於 AI API 呼叫
        self.executor = ThreadPoolExecutor(max_workers=3)
    
    def _initialize_openai_client(self) -> None:
        """初始化 OpenAI 客戶端"""
        try:
            import os
            api_key = os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY 環境變數未設定")
            
            self.openai_client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
            logger.info("✅ OpenAI 客戶端初始化成功")
        except Exception as e:
            logger.error(f"❌ OpenAI 客戶端初始化失敗: {e}")
            raise
    
    async def process_news(self, news_list: List[Dict], glossary: Dict[str, str]) -> List[Dict]:
        """
        處理新聞列表：分組 -> 排序 -> 摘要生成 -> 後處理
        
        Args:
            news_list: 過濾後的新聞列表
            glossary: 術語表字典
            
        Returns:
            處理後的新聞列表
        """
        if not news_list:
            logger.info("📭 沒有新聞需要處理")
            return []
        
        logger.info(f"🔄 開始處理 {len(news_list)} 則新聞...")
        
        # 步驟 1: 新聞分組
        grouped_news = self._group_similar_news(news_list)
        logger.info(f"📊 新聞分為 {len(grouped_news)} 組")
        
        # 步驟 2: 按重要性排序
        sorted_groups = self._sort_groups_by_importance(grouped_news)
        
        # 步驟 3: 選擇要處理的新聞組（多樣性演算法）
        selected_groups = self._select_groups_for_processing(sorted_groups)
        logger.info(f"🎯 選擇 {len(selected_groups)} 組新聞進行摘要")
        
        # 步驟 4: 並行生成摘要
        processed_news = await self._generate_summaries_batch(selected_groups, glossary)
        
        logger.info(f"✅ 新聞處理完成，產生 {len(processed_news)} 則最終新聞")
        return processed_news
    
    def _group_similar_news(self, news_list: List[Dict]) -> List[List[Dict]]:
        """將相似的新聞分組"""
        grouped_news = []
        
        for news in news_list:
            matched = False
            
            # 尋找相似的群組
            for group in grouped_news:
                similarity = SequenceMatcher(
                    None, 
                    news["title"].lower(), 
                    group[0]["title"].lower()
                ).ratio()
                
                if similarity > self.similarity_threshold:
                    group.append(news)
                    matched = True
                    break
            
            # 如果沒有找到相似群組，建立新群組
            if not matched:
                grouped_news.append([news])
        
        return grouped_news
    
    def _sort_groups_by_importance(self, grouped_news: List[List[Dict]]) -> List[List[Dict]]:
        """按重要性排序群組（以來源數量為指標）"""
        return sorted(grouped_news, key=len, reverse=True)
    
    def _select_groups_for_processing(self, sorted_groups: List[List[Dict]]) -> List[List[Dict]]:
        """使用多樣性演算法選擇要處理的新聞組"""
        selected_groups = []
        source_usage_count = {}
        
        for group in sorted_groups:
            if len(selected_groups) >= self.max_groups_to_process:
                break
            
            primary_source = group[0]['source']
            current_count = source_usage_count.get(primary_source, 0)
            
            if current_count < self.max_news_per_source:
                selected_groups.append(group)
                source_usage_count[primary_source] = current_count + 1
        
        logger.info(f"📈 各來源選取統計: {source_usage_count}")
        return selected_groups
    
    async def _generate_summaries_batch(self, news_groups: List[List[Dict]], glossary: Dict[str, str]) -> List[Dict]:
        """批次生成新聞摘要"""
        tasks = []
        
        for group in news_groups:
            task = self._generate_single_summary(group, glossary)
            tasks.append(task)
        
        # 並發執行摘要生成，但加入延遲避免 API 限流
        results = []
        for i, task in enumerate(tasks):
            if i > 0:
                await asyncio.sleep(1)  # API 呼叫間隔
            
            try:
                result = await task
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"第 {i+1} 組新聞摘要生成失敗: {e}")
        
        return results
    
    async def _generate_single_summary(self, news_group: List[Dict], glossary: Dict[str, str]) -> Optional[Dict]:
        """為單一新聞組生成摘要"""
        try:
            # 建立融合提示
            fusion_prompt = self._create_fusion_prompt(news_group, glossary)
            
            # 在線程池中呼叫 AI API
            ai_response = await asyncio.get_event_loop().run_in_executor(
                self.executor, self._call_ai_api, fusion_prompt
            )
            
            if not ai_response:
                logger.warning(f"❌ AI 未返回有效回應，跳過新聞組: {news_group[0]['title']}")
                return None
            
            # 解析並後處理 AI 回應
            processed_result = self._post_process_ai_response(ai_response, news_group, glossary)
            
            return processed_result
            
        except Exception as e:
            logger.error(f"生成摘要時發生錯誤: {e}")
            return None
    
    def _create_fusion_prompt(self, news_group: List[Dict], glossary: Dict[str, str]) -> str:
        """建立融合摘要的 AI 提示，包含術語表"""
        
        # 準備新聞素材
        input_materials = []
        for i, news_item in enumerate(news_group):
            content = news_item.get('content', '')[:2000]  # 限制內容長度
            input_materials.append(
                f"---\nSource {i+1}: {news_item['source']}\n"
                f"Title: {news_item['title']}\n"
                f"Content: {content}\n"
            )
        
        input_materials_str = "\n".join(input_materials)
        
        # 準備術語表規則
        glossary_rules = ""
        if glossary:
            glossary_items = []
            for en_term, zh_term in glossary.items():
                glossary_items.append(f"  • {en_term} → {zh_term}")
            
            glossary_rules = f"""
# MANDATORY TRANSLATION RULES (必須遵守的翻譯規則)
You MUST use these specific Taiwan translations for the following terms:
{chr(10).join(glossary_items)}
"""
        
        # 建立完整提示
        prompt = f"""# ROLE & GOAL
You are a top-tier senior editor for a major Taiwanese news outlet, tasked with producing a fast, accurate, and purely objective news brief.

{glossary_rules}

# INPUT MATERIALS
{input_materials_str.strip()}
---

# TASK
Fuse all information above. Identify the most critical facts to create an accurate overview. Your final output must be a single headline and a single summary paragraph in Traditional Chinese.

# OUTPUT REQUIREMENTS
1. **Headline (繁體中文標題):** A single, objective headline (不超過 30 字).
2. **Summary Paragraph (繁體中文摘要):** A single, cohesive paragraph between 80 and 180 characters.
3. **Quality (品質):** Use natural, modern Taiwanese Mandarin. No awkward phrases or extra spaces.
4. **VOCABULARY (詞彙):** STRICTLY follow the translation rules above.

# FINAL OUTPUT FORMAT
繁體中文標題: [Your synthesized headline here]
繁體中文摘要: [Your synthesized summary paragraph here]
"""
        
        return prompt
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=if=retry_if_exception_type((Exception,))
    )
    def _call_ai_api(self, prompt: str) -> str:
        """呼叫 AI API 生成回應"""
        try:
            model_name = self.ai_config.get('name', 'qwen/qwen-2-72b-instruct')
            temperature = self.ai_config.get('temperature', 0.6)
            max_tokens = self.ai_config.get('max_tokens', 500)
            
            response = self.openai_client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"AI API 呼叫失敗: {e}")
            raise
    
    def _post_process_ai_response(self, ai_response: str, news_group: List[Dict], glossary: Dict[str, str]) -> Dict:
        """後處理 AI 回應"""
        # 解析標題和摘要
        raw_title, raw_summary = "無標題", "無法產生摘要"
        
        for line in ai_response.split('\n'):
            line = line.strip()
            if line.startswith("繁體中文標題:"):
                raw_title = line.replace("繁體中文標題:", "").strip()
            elif line.startswith("繁體中文摘要:"):
                raw_summary = line.replace("繁體中文摘要:", "").strip()
        
        # 應用術語替換（雙重保險）
        processed_title = self._apply_term_replacements(raw_title, glossary)
        processed_summary = self._apply_term_replacements(raw_summary, glossary)
        
        # 清理文本（移除多餘空格）
        processed_summary = re.sub(r'([一-龥])\s+([一-龥])', r'\1\2', processed_summary)
        processed_summary = processed_summary.replace(' ', '')
        
        # 隨機選擇一個連結
        selected_link = random.choice(news_group)["link"]
        
        return {
            "title": processed_title,
            "summary": processed_summary,
            "link": selected_link,
            "sources_count": len(news_group),
            "primary_source": news_group[0]["source"]
        }
    
    def _apply_term_replacements(self, text: str, glossary: Dict[str, str]) -> str:
        """應用術語替換"""
        result = text
        
        # 應用術語表替換
        for english_term, taiwan_term in glossary.items():
            # 替換英文術語
            result = result.replace(english_term, taiwan_term)
        
        return result
    
    def __del__(self):
        """清理資源"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=False)
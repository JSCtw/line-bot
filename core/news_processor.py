# core/news_processor.py
# -*- coding: utf-8 -*-
"""
新聞處理器
負責新聞分組、排序、AI摘要生成與後處理，包含術語表整合
"""

import asyncio
import logging
import random
import re
from typing import Dict, List, Any, Optional
from difflib import SequenceMatcher

from utils.http_client import AsyncHTTPClient
from utils.logger import get_logger, log_async_execution_time

# 使用 get_logger 獲取與我們設定一致的 logger
logger = get_logger(__name__)

class NewsProcessor:
    """新聞處理器 - 負責摘要生成與後處理"""
    
    def __init__(self, config: Dict[str, Any], http_client: AsyncHTTPClient, sheet_manager: Any):
        self.config = config
        self.http_client = http_client
        self.sheet_manager = sheet_manager
        
        # 讀取摘要模型的設定
        self.ai_config = config.get('ai_models', {}).get('summarization', {})
        
        # 處理參數
        self.processing_config = config.get('news_processing', {})
        self.max_groups_to_process = self.processing_config.get('max_groups_to_process', 5)
        self.max_news_per_source = self.processing_config.get('max_news_per_source_in_final', 2)
        self.similarity_threshold = self.processing_config.get('similarity_threshold', 0.7)
        
    @log_async_execution_time("process_all_news")
    async def process_news(self, news_list: List[Dict], glossary: Dict[str, str]) -> List[Dict]:
        """
        處理新聞列表：分組 -> 排序 -> 選擇 -> 摘要生成 -> 過濾
        """
        if not news_list:
            logger.info("📭 沒有新聞需要處理")
            return []
        
        logger.info(f"🔄 開始處理 {len(news_list)} 則新聞...")
        
        grouped_news = self._group_similar_news(news_list)
        logger.info(f"📊 新聞分為 {len(grouped_news)} 組")
        
        sorted_groups = self._sort_groups_by_importance(grouped_news)
        
        selected_groups = self._select_groups_for_processing(sorted_groups)
        logger.info(f"🎯 選擇 {len(selected_groups)} 組新聞進行摘要")
        
        # ❗️【核心修改】使用 asyncio.gather 執行所有任務
        tasks = [self._generate_single_summary(group, glossary) for group in selected_groups]
        processed_results = await asyncio.gather(*tasks)

        # ❗️【核心修改】只保留那些沒有回傳 None 的成功結果
        final_news = [news for news in processed_results if news is not None]
        
        logger.info(f"✅ 新聞處理完成，產生 {len(final_news)} 則最終新聞")
        return final_news
    
    def _group_similar_news(self, news_list: List[Dict]) -> List[List[Dict]]:
        """將相似的新聞分組"""
        grouped_news = []
        for news in news_list:
            matched = False
            for group in grouped_news:
                # 使用 SequenceMatcher 進行標題相似度比較
                similarity = SequenceMatcher(
                    None, 
                    news["title"].lower(), 
                    group[0]["title"].lower()
                ).ratio()
                
                if similarity > self.similarity_threshold:
                    group.append(news)
                    matched = True
                    break
            
            if not matched:
                grouped_news.append([news])
        
        return grouped_news
    
    def _sort_groups_by_importance(self, grouped_news: List[List[Dict]]) -> List[List[Dict]]:
        """按重要性排序群組（以來源數量為指標）"""
        # 優先級：新聞組大小 > 隨機，以避免每次執行結果都一樣
        return sorted(grouped_news, key=lambda g: (len(g), random.random()), reverse=True)
    
    def _select_groups_for_processing(self, sorted_groups: List[List[Dict]]) -> List[List[Dict]]:
        """使用多樣性演算法選擇要處理的新聞組"""
        selected_groups = []
        source_usage_count = {}
        
        for group in sorted_groups:
            if len(selected_groups) >= self.max_groups_to_process:
                break
            
            # 以群組中第一篇新聞的來源作為代表來源
            primary_source = group[0].get('source', 'Unknown')
            current_count = source_usage_count.get(primary_source, 0)
            
            if current_count < self.max_news_per_source:
                selected_groups.append(group)
                source_usage_count[primary_source] = current_count + 1
        
        logger.info(f"📈 各來源選取統計: {source_usage_count}")
        return selected_groups
    
    async def _generate_single_summary(self, news_group: List[Dict], glossary: Dict[str, str]) -> Optional[Dict]:
        """為單一新聞組生成摘要，失敗時返回 None"""
        primary_title = news_group[0].get('title', '無標題新聞組')
        logger.info(f"📄 正在為新聞組「{primary_title}」生成摘要...")

        try:
            fusion_prompt = self._create_fusion_prompt(news_group, glossary)
            
            ai_response = await self.http_client.call_ai_api(
                prompt=fusion_prompt,
                **self.ai_config
            )
            
            if not ai_response or ai_response.strip() == "":
                logger.warning(f"⚠️ AI 模型回傳了空的摘要，已放棄新聞組: {primary_title}")
                return None
            
            processed_result = self._post_process_ai_response(ai_response, news_group, glossary)
            logger.info(f"✅ 摘要生成成功: {processed_result.get('title')}")
            return processed_result
            
        except Exception as e:
            # ❗️【核心修改】摘要失敗時，記錄詳細錯誤並回傳 None
            logger.error(f"❌ 為「{primary_title}」生成摘要時發生錯誤，已放棄此新聞組。錯誤: {e}", exc_info=True)
            return None
    
    def _create_fusion_prompt(self, news_group: List[Dict], glossary: Dict[str, str]) -> str:
        """建立融合摘要的 AI 提示，包含術語表"""
        input_materials = []
        for i, news_item in enumerate(news_group, 1):
            content = news_item.get('content', '')[:1500]
            input_materials.append(
                f"---\nSource {i}: {news_item.get('source', 'N/A')}\n"
                f"Title: {news_item.get('title', 'N/A')}\n"
                f"Content: {content}\n"
            )
        input_materials_str = "\n".join(input_materials)
        
        glossary_rules = ""
        if glossary:
            glossary_items = [f"   • {en_term} → {zh_term}" for en_term, zh_term in glossary.items()]
            glossary_rules = f"""
# MANDATORY TRANSLATION RULES (必須遵守的翻譯規則)
You MUST use these specific Taiwan translations for the following terms:
{chr(10).join(glossary_items)}
"""
        
        prompt = f"""# ROLE & GOAL
You are a top-tier senior editor for a major Taiwanese news outlet, tasked with producing a fast, accurate, and purely objective news brief.

{glossary_rules}

# INPUT MATERIALS
{input_materials_str.strip()}
---

# TASK
Fuse all information above. Identify the most critical facts to create an accurate overview. Your final output must be a single headline and a single summary paragraph in Traditional Chinese.

# OUTPUT REQUIREMENTS
1. **Headline (繁體中文標題):** A single, objective headline (max 30 characters).
2. **Summary Paragraph (繁體中文摘要):** A single, cohesive paragraph between 80 and 180 characters.
3. **Quality (品質):** Use natural, modern Taiwanese Mandarin. No awkward phrases or extra spaces.
4. **VOCABULARY (詞彙):** STRICTLY follow the translation rules above.

# FINAL OUTPUT FORMAT
繁體中文標題: [Your synthesized headline here]
繁體中文摘要: [Your synthesized summary paragraph here]
"""
        
        return prompt
    
    def _post_process_ai_response(self, ai_response: str, news_group: List[Dict], glossary: Dict[str, str]) -> Dict:
        """後處理 AI 回應"""
        raw_title, raw_summary = "【無標題】", "無法產生摘要"
        
        title_match = re.search(r"繁體中文標題:\s*(.*)", ai_response, re.IGNORECASE)
        summary_match = re.search(r"繁體中文摘要:\s*(.*)", ai_response, re.IGNORECASE)
        
        if title_match:
            raw_title = title_match.group(1).strip()
        if summary_match:
            raw_summary = summary_match.group(1).strip()

        # 如果 AI 沒有正確遵循格式，做最後的補救
        if raw_title == "【無標題】" and raw_summary == "無法產生摘要":
             lines = [line.strip() for line in ai_response.split('\n') if line.strip()]
             if len(lines) >= 2:
                 raw_title = lines[0]
                 raw_summary = " ".join(lines[1:])

        processed_title = self._apply_term_replacements(raw_title, glossary)
        processed_summary = self._apply_term_replacements(raw_summary, glossary)
        
        # 清理摘要中的多餘空格
        processed_summary = re.sub(r'\s+', ' ', processed_summary).strip()
        
        # 隨機選擇一個來源最可靠的連結 (例如，非 'Generic' 的)
        link_candidates = [news['link'] for news in news_group if 'Generic' not in news.get('source', '')]
        if not link_candidates:
            link_candidates = [news['link'] for news in news_group]

        return {
            "title": processed_title,
            "summary": processed_summary,
            "link": random.choice(link_candidates),
            "sources_count": len(news_group),
            "primary_source": news_group[0]["source"]
        }
    
    def _apply_term_replacements(self, text: str, glossary: Dict[str, str]) -> str:
        """應用術語替換，確保術語被正確翻譯"""
        for en_term, zh_term in glossary.items():
            # 使用正則表達式進行全詞匹配，避免部分替換（例如 'US' 替換掉 'Russia' 中的 'us'）
            # \b 是單詞邊界
            text = re.sub(r'\b' + re.escape(en_term) + r'\b', zh_term, text, flags=re.IGNORECASE)
        return text
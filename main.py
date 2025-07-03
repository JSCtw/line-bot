# -*- coding: utf-8 -*-
import feedparser
import requests
import time
import re
import os
import logging
import random
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from openai import OpenAI
from linebot.v3.messaging import MessagingApi, Configuration, ApiClient
from linebot.v3.messaging.models import TextMessage, PushMessageRequest

# --- 0. 基礎設定 ---

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
USER_ID = os.getenv("USER_ID")

if not all([OPENROUTER_API_KEY, LINE_CHANNEL_ACCESS_TOKEN, USER_ID]):
    raise ValueError("無法從環境變數中載入必要憑證(API_KEY, LINE_TOKEN, USER_ID)，請檢查 .env 檔案")

try:
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    api_client = ApiClient(configuration)
    line_bot_api = MessagingApi(api_client)
    logger.info("LINE Bot API 初始化成功。")
except Exception as e:
    logger.error(f"LINE Bot API 初始化失敗: {e}")
    exit()

try:
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )
    logger.info("OpenRouter Client 初始化成功。")
except Exception as e:
    logger.error(f"OpenRouter Client 初始化失敗: {e}")
    exit()

RSS_FEEDS = {
    "BBC": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "CNN": "http://rss.cnn.com/rss/edition_world.rss",
    "Fox News": "https://moxie.foxnews.com/google-publisher/world.xml"
}

# --- 1. 新聞爬取模組 ---

def fetch_rss_news():
    all_news = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                if hasattr(entry, "title") and entry.title and hasattr(entry, "link") and entry.link:
                    content = BeautifulSoup(getattr(entry, "summary", ""), "lxml").get_text(separator="\n", strip=True)
                    if len(content) < 50: continue
                    all_news.append({"source": source, "title": entry.title, "link": entry.link, "content": content})
        except Exception as e:
            logger.error(f"抓取 {source} RSS 失敗: {e}")
    return all_news

def fetch_html_news():
    url = "https://www.aljazeera.com/news/"
    all_news = []
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        articles = soup.select('article.gc.u-clickable-card')[:20]
        for article in articles:
            title_tag = article.select_one('h3.gc__title a span')
            link_tag = article.select_one('h3.gc__title a')
            
            if title_tag and link_tag:
                title = title_tag.get_text(strip=True)
                link = link_tag.get("href", "")
                if link and not link.startswith("http"):
                    link = f"https://www.aljazeera.com{link}"
                
                content_page_res = requests.get(link, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                content_soup = BeautifulSoup(content_page_res.text, "html.parser")
                content_div = content_soup.select_one('div.wysiwyg')
                if content_div:
                    content = content_div.get_text(separator="\n", strip=True)[:2000]
                    all_news.append({"source": "Al Jazeera", "title": title, "link": link, "content": content})
    except Exception as e:
        logger.error(f"抓取 Al Jazeera 新聞失敗: {e}")
    return all_news

# --- 2. AI 核心模組 ---

def create_classifier_prompt(title: str, content: str) -> str:
    """為分類任務創建專屬的 Prompt。"""
    prompt = f"""
# ROLE & GOAL
You are an expert news classification engine. Your task is to analyze the provided news article and assign two labels to it: a "Topic" label and a "Scope of Impact" label.

# INPUT
* **Title:** "{title}"
* **Content:** "{content}"

# INSTRUCTIONS
1.  Analyze the Topic: Classify it into ONE: `政治 & 外交`, `經濟 & 金融`, `軍事 & 衝突`, `科技 & 產業`, `社會 & 人文`, `災害 & 環境`, `其他`.
2.  Analyze the Scope of Impact: Classify it into ONE:
    * `全球性 (Global)`: Affects multiple continents or global systems.
    * `區域性 (Regional)`: Affects a specific region (e.g., Europe, Middle East).
    * `國內性 (Domestic)`: Affects only a single country.

# OUTPUT FORMAT
You MUST provide the output in two separate lines, exactly as follows:
Topic: [Your chosen topic classification here]
Scope: [Your chosen scope classification here]
"""
    return prompt

def create_fusion_prompt(news_list: list) -> str:
    """為融合摘要任務創建專屬的 Prompt。"""
    input_materials_str = ""
    for i, news_item in enumerate(news_list):
        input_materials_str += f"---\nSource {i+1}: {news_item['source']}\nTitle: {news_item['title']}\nContent: {news_item.get('content', '')}\n"
    
    prompt = f"""
# ROLE & GOAL
You are a top-tier senior editor for a major Taiwanese news outlet, tasked with producing a fast, accurate, and purely objective news brief.

# INPUT MATERIALS
{input_materials_str.strip()}
---

# TASK
Fuse all information above. Identify the most critical facts to create an accurate overview. Your final output must be a single headline and a single summary paragraph in Traditional Chinese.

# OUTPUT REQUIREMENTS
1.  **Headline (繁體中文標題):** A single, objective headline that synthesizes the core event.
2.  **Summary Paragraph (繁體中文摘要):** A single, cohesive paragraph between 80 and 180 characters.
3.  **Quality (品質):** Use natural, modern Taiwanese Mandarin. No awkward phrases or extra spaces.
4.  **VOCABULARY (詞彙):** Non-negotiable: All proper nouns must use standard Taiwanese translations (e.g., `Trump` is `川普`, `Gaza` is `加薩`).

# FINAL OUTPUT FORMAT (請嚴格遵守此格式輸出)
繁體中文標題: [Your synthesized headline here]
繁體中文摘要: [Your synthesized summary paragraph here]
"""
    return prompt

def get_ai_response(prompt: str, model: str, temperature: float) -> str:
    """通用的 AI API 呼叫函式。"""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"對模型 {model} 的 API 呼叫失敗: {e}")
        return ""

# --- 3. 主處理流程模組 ---

def classify_and_filter_news(news_list: list) -> list:
    """【新增】使用 AI 分類器過濾新聞，只保留重要新聞。"""
    logger.info("--- 開始執行 AI 分類與過濾 ---")
    filtered_news = []
    for news in news_list:
        # 為避免內文過長消耗 Token，只取前 1000 字元進行分類判斷
        prompt = create_classifier_prompt(news['title'], news.get('content', '')[:1000])
        # 使用輕量級模型進行快速分類
        response_text = get_ai_response(prompt, model="mistralai/mistral-7b-instruct", temperature=0.1)
        
        if not response_text:
            logger.warning(f"分類失敗，跳過新聞: {news['title']}")
            continue
        
        scope = "解析失敗"
        for line in response_text.split('\n'):
            if line.startswith("Scope:"):
                scope = line.replace("Scope:", "").strip()
        
        logger.info(f"新聞 '{news['title'][:30]}...' 的影響力被分類為: {scope}")
        
        # 核心過濾邏輯：只保留全球性或區域性的新聞
        if scope in ['全球性 (Global)', '區域性 (Regional)']:
            filtered_news.append(news)
        
        time.sleep(1) # 尊重 API
        
    logger.info(f"過濾後，剩下 {len(filtered_news)} 則具有全球或區域影響力的重要新聞。")
    return filtered_news

def process_news(news_list: list) -> list:
    """對過濾後的新聞進行分組、排序、AI摘要與後處理。"""
    if not news_list:
        return []
    
    # 分組
    grouped_news = []
    for news in news_list:
        matched = False
        for group in grouped_news:
            if SequenceMatcher(None, news["title"], group[0]["title"]).ratio() > 0.7:
                group.append(news)
                matched = True
                break
        if not matched:
            grouped_news.append([news])
    logger.info(f"重要新聞被分為 {len(grouped_news)} 組。")

    # 按重要性排序
    sorted_groups = sorted(grouped_news, key=len, reverse=True)
    logger.info("已按重要性排序。")

    # 多樣性選擇演算法
    selected_groups_for_push = []
    source_usage_count = {}
    MAX_FROM_ONE_SOURCE = 2 
    for group in sorted_groups:
        if len(selected_groups_for_push) >= 5: break
        primary_source = group[0]['source']
        if source_usage_count.get(primary_source, 0) < MAX_FROM_ONE_SOURCE:
            selected_groups_for_push.append(group)
            source_usage_count[primary_source] = source_usage_count.get(primary_source, 0) + 1
    
    logger.info(f"根據多樣性演算法，最終選擇 {len(selected_groups_for_push)} 組新聞進行摘要。")
    logger.info(f"各來源選取統計: {source_usage_count}")

    # 對選擇的群組進行 AI 處理
    final_processed_news = []
    for news_group in selected_groups_for_push:
        fusion_prompt = create_fusion_prompt(news_group)
        ai_response_text = get_ai_response(fusion_prompt, model="qwen/qwen-2-72b-instruct", temperature=0.6)
        
        if not ai_response_text: continue
            
        raw_title, raw_summary = "無標題", "無法產生摘要"
        for line in ai_response_text.split('\n'):
            if line.startswith("繁體中文標題:"):
                raw_title = line.replace("繁體中文標題:", "").strip()
            elif line.startswith("繁體中文摘要:"):
                raw_summary = line.replace("繁體中文摘要:", "").strip()
        
        processed_title = raw_title.replace("特朗普", "川普").replace("加沙", "加薩")
        processed_summary = raw_summary.replace("特朗普", "川普").replace("加沙", "加薩")
        processed_summary = re.sub(r'([一-龥])\s+([一-龥])', r'\1\2', processed_summary).replace(' ', '')
        
        link = random.choice(news_group)["link"]
        final_processed_news.append({"title": processed_title, "summary": processed_summary, "link": link})
        time.sleep(1)

    return final_processed_news

def run_news_pipeline():
    """完整的新聞處理與推播管線。"""
    logger.info("="*20 + " 啟動新聞處理管線 " + "="*20)
    
    # 步驟一：抓取所有新聞
    all_raw_news = []
    all_raw_news.extend(fetch_rss_news())
    all_raw_news.extend(fetch_html_news())
    logger.info(f"【抓取階段完成】共抓取到 {len(all_raw_news)} 則原始新聞。")

    # 步驟二：【新增】AI 分類與過濾
    important_news = classify_and_filter_news(all_raw_news)
    
    # 步驟三：處理重要新聞 (分組、AI摘要)
    processed_news_list = process_news(important_news)
    
    if not processed_news_list:
        logger.warning("沒有可供推播的新聞。")
        logger.info("="*22 + " 管線執行完畢 " + "="*22)
        return

    # 步驟四：發送新聞至 LINE
    logger.info(f"準備推播 {len(processed_news_list)} 則新聞摘要...")
    
    tz = timezone(timedelta(hours=+8))
    now = datetime.now(tz)
    hour = now.hour
    time_period = "晨間" if 5 <= hour < 12 else ("午間" if 12 <= hour < 18 else "晚間")
    date_str = now.strftime("%Y/%m/%d")
    header = f"🆕 {date_str} {time_period}國際新聞推播"
    
    try:
        line_bot_api.push_message(PushMessageRequest(to=USER_ID, messages=[TextMessage(text=header)]))
        time.sleep(1)
    except Exception as e:
        logger.error(f"推送標頭至 LINE 失敗: {e}")
        return

    for i, item in enumerate(processed_news_list):
        message_text = f"【{item['title']}】\n\n{item['summary']}\n\n{item['link']}"
        try:
            line_bot_api.push_message(PushMessageRequest(to=USER_ID, messages=[TextMessage(text=message_text)]))
            logger.info(f"已推送第 {i+1} 則新聞：{item['title']}")
            time.sleep(1)
        except Exception as e:
            logger.error(f"推送第 {i+1} 則新聞至 LINE 失敗: {e}")

    logger.info("所有新聞推送完成！")
    logger.info("="*22 + " 管線執行完畢 " + "="*22)

# --- 4. 程式主入口 ---
if __name__ == "__main__":
    run_news_pipeline()
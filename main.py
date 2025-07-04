# -*- coding: utf-8 -*-
import feedparser
import requests
import time
import re
import os
import logging
import random
import gspread
from google.oauth2.service_account import Credentials
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
GOOGLE_SHEET_NAME = "每日新聞推播記錄"

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

# --- Google Sheets 核心模組 ---

# ---【新增】Google Sheets 核心模組 ---
def get_gspread_client():
    """【無金鑰版】初始化 gspread 客戶端，在 Cloud Run 環境下會自動驗證。"""
    try:
        # 在 Cloud Run 環境中，gspread 會自動尋找並使用 Runtime Service Account 的權限
        creds = Credentials.from_service_account_file("gcp_credentials.json") # 本地測試時使用
        client = gspread.authorize(creds)
        logger.info("本地端 Google Sheets API 驗證成功。")
        return client
    except FileNotFoundError:
        # 如果找不到本地金鑰檔，則嘗試使用雲端環境的預設權限
        logger.info("未找到本地金鑰檔，嘗試使用雲端環境預設權限...")
        client = gspread.service_account()
        logger.info("雲端環境 Google Sheets API 驗證成功。")
        return client
    except Exception as e:
        logger.error(f"無法初始化 Google Sheets 客戶端: {e}")
        return None

# ---不變 ---
def get_sent_links(worksheet) -> set:
    try:
        links = worksheet.col_values(4) # Link 欄位在第 D 欄
        logger.info(f"從 Google Sheet 讀取到 {len(links) - 1} 筆已發送記錄。")
        return set(links[1:])
    except Exception as e:
        logger.error(f"讀取 Google Sheet 歷史連結失敗: {e}")
        return set()

def log_sent_news(worksheet, news_items: list):
    try:
        tz = timezone(timedelta(hours=+8))
        rows_to_append = []
        for item in news_items:
            timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
            rows_to_append.append([timestamp, item['title'], item['summary'], item['link']])
        
        if rows_to_append:
            worksheet.append_rows(rows_to_append, value_input_option='USER_ENTERED')
            logger.info(f"已將 {len(rows_to_append)} 則新紀錄寫入 Google Sheet。")
    except Exception as e:
        logger.error(f"寫入 Google Sheet 失敗: {e}")

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
                try:
                    content_page_res = requests.get(link, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                    content_soup = BeautifulSoup(content_page_res.text, "html.parser")
                    content_div = content_soup.select_one('div.wysiwyg')
                    if content_div:
                        content = content_div.get_text(separator="\n", strip=True)[:2000]
                        all_news.append({"source": "Al Jazeera", "title": title, "link": link, "content": content})
                except Exception as page_e:
                    logger.warning(f"無法深入抓取 Al Jazeera 頁面 {link}: {page_e}")
    except Exception as e:
        logger.error(f"抓取 Al Jazeera 新聞列表失敗: {e}")
    return all_news


# --- 2. AI 核心模組 ---

def create_classifier_prompt(title: str, content: str) -> str:
    """為分類任務創建專屬的「範例學習」Prompt。"""
    prompt = f"""You are a news classification expert. Your task is to classify the following news article by its "Topic" and its "Scope of Impact". Follow the output format of the examples precisely.

---
**Example 1:**
**Input Article:**
Title: Federal Reserve signals potential interest rate hike amid inflation fears
Content: The US Federal Reserve hinted at another potential interest rate hike to combat persistent inflation. This move is expected to impact global financial markets...
**Output:**
Topic: 經濟 & 金融
Scope: 全球性

---
**Example 2:**
**Input Article:**
Title: Jury selection begins in high-profile Diddy sex trafficking trial in New York
Content: Jury selection has started in the highly anticipated sex trafficking trial of music mogul Sean 'Diddy' Combs. The trial, taking place in a New York federal court, is focused on allegations from multiple plaintiffs...
**Output:**
Topic: 社會 & 人文
Scope: 國內性

---
**Article to Classify:**
**Input Article:**
Title: {title}
Content: {content}
**Output:**
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
1.  **Headline (繁體中文標題):** A single, objective headline.
2.  **Summary Paragraph (繁體中文摘要):** A single, cohesive paragraph between 80 and 180 characters.
3.  **Quality (品質):** Use natural, modern Taiwanese Mandarin. No awkward phrases or extra spaces.
4.  **VOCABULARY (詞彙):** Non-negotiable: All proper nouns must use standard Taiwanese translations (e.g., `Trump` is `川普`, `Gaza` is `加薩`).

# FINAL OUTPUT FORMAT
繁體中文標題: [Your synthesized headline here]
繁體中文摘要: [Your synthesized summary paragraph here]
"""
    return prompt

def get_ai_response(prompt: str, model: str, temperature: float = None) -> str:
    """通用的 AI API 呼叫函式。"""
    try:
        request_params = { "model": model, "messages": [{"role": "user", "content": prompt}] }
        if temperature is not None:
            request_params["temperature"] = temperature
        response = client.chat.completions.create(**request_params)
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"對模型 {model} 的 API 呼叫失敗: {e}")
        return ""

# --- 3. 主處理流程模組 ---

def classify_and_filter_news(news_list: list, sent_links: set) -> list:
    """【最終擴充版】擴充了解析關鍵字，以應對更多樣的 AI 回應。"""
    logger.info("--- 開始執行 AI 分類與初步過濾 ---")
    
    unsent_news = [news for news in news_list if news.get("link") not in sent_links]
    logger.info(f"從 {len(news_list)} 則原始新聞中，移除了 {len(news_list) - len(unsent_news)} 則已發送過的新聞。")
    
    filtered_news = []
    for news in unsent_news:
        prompt = create_classifier_prompt(news['title'], news.get('content', '')[:1000])
        response_text = get_ai_response(prompt, model="mistralai/mistral-7b-instruct", temperature=0.1)
        
        logger.info(f"AI 分類器對 '{news['title'][:30]}...' 的原始回應: '{response_text}'")

        if not response_text:
            logger.warning(f"分類失敗，跳過新聞: {news['title']}")
            continue
        
        scope = "解析失敗"
        response_lower = response_text.lower()
        
        # --- 【修改】擴充關鍵字列表 ---
        global_keywords = ['global', '全球性', '國際性']
        # 新增了 '地區', '區域', '國外', 'european' 等詞來增加匹配成功率
        regional_keywords = ['regional', '區域性', '局部性', '國外', '地區', '區域', 'european'] 
        domestic_keywords = ['domestic', '國內性']
        # --------------------------------

        is_global = any(keyword in response_text for keyword in global_keywords)
        is_regional = any(keyword in response_text or keyword in response_lower for keyword in regional_keywords)
        is_domestic = any(keyword in response_text.lower() or keyword in response_text for keyword in domestic_keywords)

        # 按優先級賦值
        if is_global:
            scope = '全球性 (Global)'
        elif is_regional:
            scope = '區域性 (Regional)'
        elif is_domestic:
            scope = '國內性 (Domestic)'
        
        logger.info(f"新聞 '{news['title'][:30]}...' 的影響力被【最終解析】為: {scope}")
        
        if scope in ['全球性 (Global)', '區域性 (Regional)']:
            filtered_news.append(news)
        
        time.sleep(1)
        
    logger.info(f"過濾後，剩下 {len(filtered_news)} 則具有全球或區域影響力的重要新聞。")
    return filtered_news

def process_news(news_list: list) -> list:
    """對過濾後的新聞進行分組、排序、AI摘要與後處理。"""
    if not news_list: return []
    
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

    sorted_groups = sorted(grouped_news, key=len, reverse=True)
    logger.info("已按重要性 (來源數) 排序。")

    selected_groups_for_push, source_usage_count = [], {}
    MAX_FROM_ONE_SOURCE = 2
    for group in sorted_groups:
        if len(selected_groups_for_push) >= 5: break
        primary_source = group[0]['source']
        if source_usage_count.get(primary_source, 0) < MAX_FROM_ONE_SOURCE:
            selected_groups_for_push.append(group)
            source_usage_count[primary_source] = source_usage_count.get(primary_source, 0) + 1
    
    logger.info(f"據多樣性演算法，選擇 {len(selected_groups_for_push)} 組新聞進行摘要。各來源選取統計: {source_usage_count}")

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
        
        # 後處理
        processed_title = raw_title.replace("特朗普", "川普").replace("加沙", "加薩")
        processed_summary = raw_summary.replace("特朗普", "川普").replace("加沙", "加薩")
        processed_summary = re.sub(r'([一-龥])\s+([一-龥])', r'\1\2', processed_summary).replace(' ', '')
        
        link = random.choice(news_group)["link"]
        final_processed_news.append({"title": processed_title, "summary": processed_summary, "link": link})
        time.sleep(1)

    return final_processed_news

# --- 4. 完整管線與主入口 ---

def run_news_pipeline():
    """完整的新聞處理與推播管線。"""
    logger.info("="*20 + " 啟動新聞處理管線 " + "="*20)

    gs_client = get_gspread_client()
    if not gs_client:
        logger.error("無法繼續執行，因 Google Sheet 客戶端初始化失敗。")
        return
    worksheet = gs_client.open(GOOGLE_SHEET_NAME).worksheet("sent_news_log")
    sent_links_set = get_sent_links(worksheet)
    
    all_raw_news = []
    all_raw_news.extend(fetch_rss_news())
    all_raw_news.extend(fetch_html_news())
    logger.info(f"【抓取階段完成】共抓取到 {len(all_raw_news)} 則原始新聞。")

    important_news = classify_and_filter_news(all_raw_news, sent_links_set)
    
    processed_news_list = process_news(important_news)
    
    if not processed_news_list:
        logger.warning("沒有可供推播的新聞。")
        logger.info("="*22 + " 管線執行完畢 " + "="*22)
        return

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
    
    log_sent_news(worksheet, processed_news_list)

    logger.info("所有新聞推送完成！")
    logger.info("="*22 + " 管線執行完畢 " + "="*22)

if __name__ == "__main__":
    run_news_pipeline()
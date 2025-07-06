# -*- coding: utf-8 -*-
import feedparser
import requests
import time
import re
import os
import logging
import random
import gspread
import google.auth # <--- 【新增】匯入 google.auth 模組
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from openai import OpenAI
from linebot.v3.messaging import MessagingApi, Configuration, ApiClient
from linebot.v3.messaging.models import TextMessage, PushMessageRequest
from flask import Flask

# ==============================================================================
# 0. 基礎設定 (在所有函式與應用程式邏輯之前)
# ==============================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
USER_ID = os.getenv("USER_ID")
GOOGLE_SHEET_NAME = "每日新聞推播記錄"

# 檢查本地 .env 是否設定齊全 (主要用於本地測試)
if os.environ.get("IS_CLOUD_RUN") != "true":
    if not all([OPENROUTER_API_KEY, LINE_CHANNEL_ACCESS_TOKEN, USER_ID]):
        raise ValueError("本地環境：無法從 .env 載入必要憑證(API_KEY, LINE_TOKEN, USER_ID)")

# 初始化所有 API Client
try:
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    api_client = ApiClient(configuration)
    line_bot_api = MessagingApi(api_client)
    logger.info("LINE Bot API 初始化成功。")

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )
    logger.info("OpenRouter Client 初始化成功。")
except Exception as e:
    logger.error(f"API Client 初始化失敗: {e}")
    exit()

RSS_FEEDS = {
    "BBC": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "CNN": "http://rss.cnn.com/rss/edition_world.rss",
    "Fox News": "https://moxie.foxnews.com/google-publisher/world.xml"
}

# ==============================================================================
# 1. 所有函式定義 (Helper Functions & Core Logic)
# ==============================================================================

def get_gspread_client():
    """【最終正確版】使用應用程式預設憑證 (ADC) 進行驗證。"""
    logger.info("正在使用應用程式預設憑證 (ADC) 進行驗證...")
    try:
        # 在 Cloud Run 環境中，這個方法會自動使用附加的服務帳戶權限
        # 在本地端，它會尋找您透過 `gcloud auth application-default login` 設定的權限
        creds, _ = google.auth.default(
            scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        )
        client = gspread.authorize(creds)
        logger.info("Google API Client 驗證成功！")
        return client
    except Exception as e:
        logger.error(f"使用 ADC 進行驗證失敗: {e}")
        raise e

def get_sent_links(worksheet):
    """從工作表中讀取所有已發送的連結並返回一個集合。"""
    try:
        links = worksheet.col_values(4) # Link 欄位在第 D 欄
        logger.info(f"從 Google Sheet 讀取到 {len(links) - 1} 筆已發送記錄。")
        return set(links[1:])
    except gspread.exceptions.WorksheetNotFound:
        logger.warning(f"在試算表中找不到名為 '{worksheet.title}' 的工作表，將自動建立。")
        worksheet.spreadsheet.add_worksheet(title=worksheet.title, rows="1", cols="4")
        # 需要重新獲取工作表對象
        new_worksheet = worksheet.spreadsheet.worksheet(worksheet.title)
        new_worksheet.update('A1:D1', [['Timestamp', 'Title', 'Summary', 'Link']])
        logger.info(f"已建立新的工作表 '{worksheet.title}' 並設定表頭。")
        return set()
    except Exception as e:
        logger.error(f"讀取 Google Sheet 歷史連結失敗: {e}")
        return set()

def log_sent_news(worksheet, news_items: list):
    """將成功發送的新聞記錄寫入工作表。"""
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

def fetch_rss_news():
    """從 RSS 來源抓取新聞"""
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
    """從 Al Jazeera 靜態 HTML 頁面抓取新聞"""
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

def classify_and_filter_news(news_list: list, sent_links: set) -> list:
    """使用 AI 分類器過濾新聞，並移除已發送過的新聞。"""
    logger.info("--- 開始執行 AI 分類與初步過濾 ---")
    
    unsent_news = [news for news in news_list if news.get("link") not in sent_links]
    logger.info(f"從 {len(news_list)} 則原始新聞中，移除了 {len(news_list) - len(unsent_news)} 則已發送過的新聞。")
    
    filtered_news = []
    for news in unsent_news:
        prompt = create_classifier_prompt(news['title'], news.get('content', '')[:1000])
        response_text = get_ai_response(prompt, model="mistralai/mistral-7b-instruct", temperature=0.1)
        
        if not response_text:
            logger.warning(f"分類器未回傳內容，跳過新聞: {news['title']}")
            continue

        scope = "解析失敗"
        global_keywords, regional_keywords, domestic_keywords = ['global', '全球性', '國際性'], ['regional', '區域性', '局部性', '國外', '地區', '區域', 'european'], ['domestic', '國內性']
        is_global = any(keyword in response_text for keyword in global_keywords)
        is_regional = any(keyword in response_text or keyword in response_text.lower() for keyword in regional_keywords)
        is_domestic = any(keyword in response_text.lower() or keyword in response_text for keyword in domestic_keywords)

        if is_global: scope = '全球性 (Global)'
        elif is_regional: scope = '區域性 (Regional)'
        elif is_domestic: scope = '國內性 (Domestic)'
        
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
                group.append(news); matched = True; break
        if not matched: grouped_news.append([news])
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
            if line.startswith("繁體中文標題:"): raw_title = line.replace("繁體中文標題:", "").strip()
            elif line.startswith("繁體中文摘要:"): raw_summary = line.replace("繁體中文摘要:", "").strip()
        
        processed_title = raw_title.replace("特朗普", "川普").replace("加沙", "加薩")
        processed_summary = raw_summary.replace("特朗普", "川普").replace("加沙", "加薩")
        processed_summary = re.sub(r'([一-龥])\s+([一-龥])', r'\1\2', processed_summary).replace(' ', '')
        
        link = random.choice(news_group)["link"]
        final_processed_news.append({"title": processed_title, "summary": processed_summary, "link": link})
        time.sleep(1)

    return final_processed_news

def run_news_pipeline():
    """【最終穩健版】完整的新聞處理與推播管線，強化了首次運行的穩定性。"""
    logger.info("="*20 + " 啟動新聞處理管線 " + "="*20)
    
    # 步驟一：初始化 Google Sheet 連線
    gs_client = get_gspread_client()
    if not gs_client:
        logger.error("無法繼續執行，因 Google Sheet 客戶端初始化失敗。")
        return

    # 步驟二：【強化】開啟或建立試算表與工作表
    try:
        spreadsheet = gs_client.open(GOOGLE_SHEET_NAME)
    except gspread.exceptions.SpreadsheetNotFound:
        logger.warning(f"找不到名為 '{GOOGLE_SHEET_NAME}' 的試算表，將建立一個新的。")
        spreadsheet = gs_client.create(GOOGLE_SHEET_NAME)
        # 重要：新建的檔案需要手動將您的服務帳戶 Email 加入共用編輯者
        # 您可以在 GCP Console -> IAM -> 服務帳戶中找到這個 Email
        logger.warning(f"重要！請手動將您的服務帳戶 Email 加入到新的試算表 '{GOOGLE_SHEET_NAME}' 的共用權限中。")

    try:
        worksheet = spreadsheet.worksheet("sent_news_log")
    except gspread.exceptions.WorksheetNotFound:
        logger.warning(f"在試算表中找不到名為 'sent_news_log' 的工作表，將建立一個新的。")
        # 預設第一個工作表名為 'Sheet1'，我們將其重新命名
        worksheet = spreadsheet.worksheet("Sheet1")
        worksheet.update_title("sent_news_log")
        worksheet.update('A1:D1', [['Timestamp', 'Title', 'Summary', 'Link']])

    # 步驟三：讀取歷史連結
    sent_links_set = get_sent_links(worksheet)
    
    # 步驟四：抓取所有新聞
    all_raw_news = []
    all_raw_news.extend(fetch_rss_news())
    all_raw_news.extend(fetch_html_news())
    logger.info(f"【抓取階段完成】共抓取到 {len(all_raw_news)} 則原始新聞。")

    # 步驟五：分類與過濾
    important_news = classify_and_filter_news(all_raw_news, sent_links_set)
    
    # 步驟六：摘要與處理
    processed_news_list = process_news(important_news)
    
    if not processed_news_list:
        logger.warning("沒有可供推播的新聞。")
        logger.info("="*22 + " 管線執行完畢 " + "="*22)
        return

    # 步驟七：發送至 LINE
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
    
    # 步驟八：記錄到 Google Sheet
    log_sent_news(worksheet, processed_news_list)

    logger.info("所有新聞推送完成！")
    logger.info("="*22 + " 管線執行完畢 " + "="*22)

# ==============================================================================
# 2. Flask App 定義 (Gunicorn 的進入點)
# ==============================================================================

app = Flask(__name__)

@app.route("/", methods=["POST", "GET"])
def main_handler():
    """【終極除錯版】捕捉詳細錯誤並直接回傳。"""
    try:
        run_news_pipeline()
        return "Pipeline executed successfully.", 200
    except Exception as e:
        # 捕捉完整的 Traceback 字串
        error_traceback = traceback.format_exc()
        
        # 在日誌中記錄更詳細的錯誤，方便我們查看
        logger.error(f"FATAL ERROR during pipeline execution:\n{error_traceback}")
        
        # 將詳細錯誤訊息作為 HTTP 回應的一部分傳回，以便在 Scheduler 日誌中看到
        # 限制長度以避免超過 HTTP 回應大小限制
        return f"An internal error occurred:\n{error_traceback[:4000]}", 500


# ==============================================================================
# 3. 本地端執行入口 (直接用 python main.py 執行)
# ==============================================================================

if __name__ == "__main__":
    logger.info("偵測到本地端直接執行，開始執行一次新聞管線...")
    run_news_pipeline()
# -*- coding: utf-8 -*-
import feedparser
import requests
import time
import re
import os
import logging
import random
import gspread
import google.auth
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
# 0. 基礎設定
# ==============================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
USER_ID = os.getenv("USER_ID")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL") 
CLASSIFIER_BATCH_SIZE = 10

# 在雲端環境中，環境變數由 Cloud Run 提供，此檢查主要用於本地
if os.environ.get("IS_CLOUD_RUN") != "true":
    if not all([OPENROUTER_API_KEY, LINE_CHANNEL_ACCESS_TOKEN, USER_ID, GOOGLE_SHEET_URL]):
        raise ValueError("本地環境：無法從 .env 載入所有必要憑證，包括 GOOGLE_SHEET_URL")

# 初始化所有 API Client
try:
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    api_client = ApiClient(configuration)
    line_bot_api = MessagingApi(api_client)
    logger.info("LINE Bot API 初始化成功。")
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
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
# 1. 所有函式定義
# ==============================================================================

def get_gspread_client():
    logger.info("正在使用應用程式預設憑證 (ADC) 進行驗證...")
    try:
        creds, _ = google.auth.default(scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
        client = gspread.authorize(creds)
        logger.info("Google API Client 驗證成功！")
        return client
    except Exception as e:
        logger.error(f"使用 ADC 進行驗證失敗: {e}")
        raise e

def get_sent_links(worksheet):
    try:
        links = worksheet.col_values(4)
        logger.info(f"從 Google Sheet 讀取到 {len(links) - 1} 筆已發送記錄。")
        return set(links[1:])
    except gspread.exceptions.WorksheetNotFound:
        logger.warning(f"在試算表中找不到名為 '{worksheet.title}' 的工作表，將自動建立。")
        worksheet.spreadsheet.add_worksheet(title=worksheet.title, rows="1", cols="4")
        new_worksheet = worksheet.spreadsheet.worksheet(worksheet.title)
        new_worksheet.update('A1:D1', [['Timestamp', 'Title', 'Summary', 'Link']])
        return set()
    except Exception as e:
        logger.error(f"讀取 Google Sheet 歷史連結失敗: {e}")
        return set()

def get_translation_glossary(spreadsheet) -> dict:
    try:
        worksheet = spreadsheet.worksheet("glossary")
        records = worksheet.get_all_records()
        glossary = {item['English_Term']: item['Taiwan_Term'] for item in records if item.get('English_Term')}
        logger.info(f"成功從 Google Sheet 讀取到 {len(glossary)} 筆術語。")
        return glossary
    except gspread.exceptions.WorksheetNotFound:
        logger.warning("在試算表中找不到名為 'glossary' 的工作表，將使用空術語表。")
        return {}
    except Exception as e:
        logger.error(f"讀取術語表失敗: {e}")
        return {}

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

def fetch_rss_news():
    all_news, MAX_NEWS_AGE_DAYS = [], 3
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                if not (hasattr(entry, "title") and entry.title and hasattr(entry, "link") and entry.link): continue
                is_recent = False
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    article_dt = datetime(*entry.published_parsed[:6])
                    if datetime.now() - article_dt < timedelta(days=MAX_NEWS_AGE_DAYS):
                        is_recent = True
                    else:
                        logger.info(f"過濾掉過舊 RSS 新聞 (發布於 {article_dt.date()})：{entry.title}")
                else:
                    logger.warning(f"RSS 新聞缺少發布日期，予以跳過：{entry.title}")
                if not is_recent: continue
                content = BeautifulSoup(getattr(entry, "summary", ""), "lxml").get_text(separator="\n", strip=True)
                if len(content) < 50: continue
                all_news.append({"source": source, "title": entry.title, "link": entry.link, "content": content})
        except Exception as e:
            logger.error(f"抓取 {source} RSS 失敗: {e}")
    return all_news

def fetch_html_news():
    all_news = []
    url = "https://www.aljazeera.com/news/"
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
                if link and not link.startswith("http"): link = f"https://www.aljazeera.com{link}"
                all_news.append({"source": "Al Jazeera", "title": title, "link": link, "content": title}) # 暫用標題當內容
    except Exception as e:
        logger.error(f"抓取 Al Jazeera 新聞列表失敗: {e}")
    return all_news

def create_batch_classifier_prompt(news_batch: list) -> str:
    input_articles_str = ""
    for i, news_item in enumerate(news_batch):
        content_snippet = news_item.get('content', '')[:500]
        input_articles_str += f"---\nArticle {i+1}:\nTitle: {news_item['title']}\nContent: {content_snippet}\n"
    prompt = f"""You are a news classification expert. Your task is to classify each of the following {len(news_batch)} articles.
For each article, you MUST provide its Topic and Scope on a new line, using the exact format: "Article [index]: Topic: [Topic] | Scope: [Scope]".
Follow the output format of the examples precisely.
---
**Example 1:**
Input Article:
Title: Federal Reserve signals potential interest rate hike amid inflation fears
Content: The US Federal Reserve hinted at another potential interest rate hike...
**Output:**
Article 1: Topic: 經濟 & 金融 | Scope: 全球性
---
**Example 2:**
Input Article:
Title: Jury selection begins in high-profile Diddy sex trafficking trial in New York
Content: Jury selection has started in the highly anticipated sex trafficking trial...
**Output:**
Article 2: Topic: 社會 & 人文 | Scope: 國內性
---
**Articles to Classify:**
{input_articles_str.strip()}

**Your Output:**
"""
    return prompt

def create_fusion_prompt(news_list: list, glossary: dict) -> str:
    input_materials_str = ""
    for i, news_item in enumerate(news_list):
        input_materials_str += f"---\nSource {i+1}: {news_item['source']}\nTitle: {news_item['title']}\nContent: {news_item.get('content', '')}\n"
    glossary_rules = "\n".join([f"- {en} -> {tw}" for en, tw in glossary.items()])
    prompt = f"""
# ROLE & GOAL
You are a top-tier senior editor for a major Taiwanese news outlet, tasked with producing a fast, accurate, and purely objective news brief.
# VOCABULARY & TRANSLATION RULES
This is a non-negotiable editorial standard. You MUST use the following translations for any mentioned proper nouns:
{glossary_rules}
# INPUT MATERIALS
{input_materials_str.strip()}
---
# TASK
Fuse all information above. Your final output must be a single headline and a single summary paragraph in Traditional Chinese.
# OUTPUT REQUIREMENTS
1.  Headline: A single, objective headline.
2.  Summary Paragraph: A single, cohesive paragraph between 80 and 180 characters.
3.  Quality: Use natural, modern Taiwanese Mandarin. No awkward phrases or extra spaces.
# FINAL OUTPUT FORMAT
繁體中文標題: [Your synthesized headline here]
繁體中文摘要: [Your synthesized summary paragraph here]
"""
    return prompt

def get_ai_response(prompt: str, model: str, temperature: float = None) -> str:
    try:
        request_params = { "model": model, "messages": [{"role": "user", "content": prompt}] }
        if temperature is not None: request_params["temperature"] = temperature
        response = client.chat.completions.create(**request_params)
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"對模型 {model} 的 API 呼叫失敗: {e}")
        return ""

def classify_and_filter_news(news_list: list, sent_links: set) -> list:
    logger.info("--- 開始執行 AI 批次分類與初步過濾 ---")
    unsent_news = [news for news in news_list if news.get("link") not in sent_links]
    if not unsent_news: return []
    
    batches = [unsent_news[i:i + CLASSIFIER_BATCH_SIZE] for i in range(0, len(unsent_news), CLASSIFIER_BATCH_SIZE)]
    logger.info(f"待分類新聞共 {len(unsent_news)} 則，分為 {len(batches)} 個批次處理。")
    
    classified_news = {}
    for i, batch in enumerate(batches):
        logger.info(f"正在處理第 {i+1}/{len(batches)} 批次...")
        prompt = create_batch_classifier_prompt(batch)
        response_text = get_ai_response(prompt, model="mistralai/mistral-7b-instruct", temperature=0.1)
        if not response_text: continue
        
        pattern = re.compile(r"Article\s+(\d+):\s*Topic:(.*?)\|.*?Scope:(.*)")
        for line in response_text.split('\n'):
            match = pattern.search(line)
            if match:
                try:
                    article_index, scope_raw = int(match.group(1)) - 1, match.group(3).strip()
                    if 0 <= article_index < len(batch):
                        news_item = batch[article_index]
                        news_item['scope_raw'] = scope_raw
                        classified_news[news_item['link']] = news_item
                except (ValueError, IndexError): continue
        time.sleep(1)

    logger.info(f"AI 分類完成，共 {len(classified_news)} 則新聞獲得了分類標籤。")
    
    filtered_news = []
    for news in classified_news.values():
        scope, scope_raw, scope_raw_lower = "解析失敗", news.get('scope_raw', ''), news.get('scope_raw', '').lower()
        global_keywords, regional_keywords, domestic_keywords = ['global', '全球性', '國際性'], ['regional', '區域性', '局部性', '國外', '地區', '區域', 'european'], ['domestic', '國內性']
        
        if any(k in scope_raw for k in global_keywords): scope = '全球性 (Global)'
        elif any(k in scope_raw or k in scope_raw_lower for k in regional_keywords): scope = '區域性 (Regional)'
        elif any(k in scope_raw_lower or k in scope_raw for k in domestic_keywords): scope = '國內性 (Domestic)'
        
        if scope in ['全球性 (Global)', '區域性 (Regional)']:
            filtered_news.append(news)
            
    logger.info(f"過濾後，剩下 {len(filtered_news)} 則具有全球或區域影響力的重要新聞。")
    return filtered_news

def process_news(news_list: list, glossary: dict) -> list:
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
        fusion_prompt = create_fusion_prompt(news_group, glossary)
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
    logger.info("="*20 + " 啟動新聞處理管線 " + "="*20)
    gs_client = get_gspread_client()
    if not gs_client:
        logger.error("無法繼續執行，因 Google Sheet 客戶端初始化失敗。"); return

    try:
        spreadsheet = gs_client.open_by_url(GOOGLE_SHEET_URL)
        log_worksheet = spreadsheet.worksheet("sent_news_log")
        translation_glossary = get_translation_glossary(spreadsheet)
    except Exception as e:
        logger.error(f"開啟 Google Sheet 或讀取工作表失敗: {e}"); return
    
    sent_links_set = get_sent_links(log_worksheet)
    
    all_raw_news = []
    all_raw_news.extend(fetch_rss_news())
    all_raw_news.extend(fetch_html_news())
    logger.info(f"【抓取階段完成】共抓取到 {len(all_raw_news)} 則原始新聞。")

    important_news = classify_and_filter_news(all_raw_news, sent_links_set)
    processed_news_list = process_news(important_news, translation_glossary)
    
    if not processed_news_list:
        logger.warning("沒有可供推播的新聞。"); logger.info("="*22 + " 管線執行完畢 " + "="*22"); return

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
        logger.error(f"推送標頭至 LINE 失敗: {e}"); return

    for i, item in enumerate(processed_news_list):
        message_text = f"【{item['title']}】\n\n{item['summary']}\n\n{item['link']}"
        try:
            line_bot_api.push_message(PushMessageRequest(to=USER_ID, messages=[TextMessage(text=message_text)]))
            logger.info(f"已推送第 {i+1} 則新聞：{item['title']}")
            time.sleep(1)
        except Exception as e:
            logger.error(f"推送第 {i+1} 則新聞至 LINE 失敗: {e}")
    
    log_sent_news(log_worksheet, processed_news_list)
    logger.info("所有新聞推送完成！"); logger.info("="*22 + " 管線執行完畢 " + "="*22)

# ==============================================================================
# 2. Flask App 定義 (Gunicorn 的進入點)
# ==============================================================================

app = Flask(__name__)

@app.route("/", methods=["POST", "GET"])
def main_handler():
    try:
        run_news_pipeline()
        return "Pipeline executed successfully.", 200
    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"FATAL ERROR during pipeline execution:\n{error_traceback}")
        return f"An internal error occurred:\n{error_traceback[:4000]}", 500

# ==============================================================================
# 3. 本地端執行入口 (直接用 python main.py 執行)
# ==============================================================================

if __name__ == "__main__":
    logger.info("偵測到本地端直接執行，開始執行一次新聞管線...")
    run_news_pipeline()
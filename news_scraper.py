import feedparser
import requests
import time
import re
import os
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from langdetect import detect
from playwright.sync_api import sync_playwright
from deep_translator import GoogleTranslator  # 保留作為備用
from difflib import SequenceMatcher
from collections import Counter
from linebot.v3.messaging import MessagingApi, Configuration, ApiClient
from linebot.v3.messaging.models import TextMessage, PushMessageRequest
import logging
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# 設置日誌
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 載入 .env 檔案
load_dotenv()
GROK_API_KEY = os.getenv("GROK_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not all([GROK_API_KEY, LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET]):
    raise ValueError("無法從環境變數中載入必要憑證，請檢查 .env 檔案是否正確設置")

# 初始化 LINE Bot
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)

RSS_FEEDS = {
    "BBC": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "CNN": "http://rss.cnn.com/rss/edition_world.rss",
    "Fox News": "https://moxie.foxnews.com/google-publisher/world.xml"
}

EXCLUDED_TW_SOURCES = [
    "https://www.thenewslens.com",
    "https://tw.news.yahoo.com",
    "https://www.cna.com.tw"
]

def clean_text(text):
    """清理文本，移除多餘字符"""
    unwanted_patterns = [r"---.*", r"[-\*=]+", r"<|eos|>", r"\s+"]
    for pattern in unwanted_patterns:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    return text.strip()

def fetch_rss_news():
    all_news = []
    for source, url in RSS_FEEDS.items():
        if any(excluded in url for excluded in EXCLUDED_TW_SOURCES):
            continue
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                all_news.append({
                    "source": source,
                    "title": entry.title if hasattr(entry, "title") else "無標題",
                    "link": entry.link if hasattr(entry, "link") else "",
                    "summary": getattr(entry, "summary", "")
                })
        except Exception as e:
            logger.error(f"抓取 {source} RSS 失敗: {e}")
    return all_news

def fetch_html_news():
    url = "https://www.aljazeera.com/news/"
    if any(excluded in url for excluded in EXCLUDED_TW_SOURCES):
        return []
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        all_news = []
        for article in soup.find_all("article")[:5]:
            title_tag = article.find("h3")
            title = title_tag.text.strip() if title_tag else "無標題"
            link_tag = article.find("a")
            link = link_tag.get("href") if link_tag else ""
            if link and not link.startswith("http"):
                link = f"https://www.aljazeera.com{link}"
            summary_tag = article.find("p")
            summary = summary_tag.text.strip() if summary_tag else ""
            all_news.append({"source": "Al Jazeera", "title": title, "link": link, "summary": summary})
        return all_news
    except Exception as e:
        logger.error(f"抓取 Al Jazeera 新聞失敗: {e}")
        return []

def fetch_dynamic_news():
    url = "https://www.wsj.com/news/world"
    if any(excluded in url for excluded in EXCLUDED_TW_SOURCES):
        return []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=60000)
            page.wait_for_selector("h2 a", timeout=15000)
            articles = page.query_selector_all("h2 a")[:5]
            news_items = [(a.inner_text(), a.get_attribute("href")) for a in articles]
            all_news = []
            for title, link in news_items:
                page.goto(link, timeout=60000)
                page.wait_for_selector("p", timeout=15000)
                summary = page.query_selector("p").inner_text()[:500]
                all_news.append({"source": "WSJ", "title": title, "link": link, "summary": summary})
            browser.close()
            return all_news
    except Exception as e:
        logger.error(f"抓取 WSJ 新聞失敗: {e}")
        return []

def remove_duplicates(news_list, threshold=0.8):
    unique_news = []
    seen_titles = []
    for news in news_list:
        title = news["title"]
        is_duplicate = any(SequenceMatcher(None, title, seen).ratio() > threshold for seen in seen_titles)
        if not is_duplicate:
            seen_titles.append(title)
            unique_news.append(news)
    return unique_news

def detect_language(text):
    try:
        return detect(text)
    except:
        return "unknown"

def translate_to_traditional_chinese(text):
    if not text or detect_language(text) == "zh-tw":
        return text
    try:
        session = requests.Session()
        retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[404, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        response = session.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "deepseek:latest",
                "prompt": f"Translate to Traditional Chinese: '{text}'",
                "stream": False
            },
            timeout=10  # 設置 10 秒超時
        )
        response.raise_for_status()
        return response.json()["response"].strip()
    except Exception as e:
        logger.error(f"Ollama 翻譯失敗: {e}")
        return GoogleTranslator(source="auto", target="zh-TW").translate(text)  # 備用翻譯


def summarize_news(content):
    try:
        session = requests.Session()
        retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        endpoint = "https://api.x.ai/v1/completions"
        headers = {"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "grok-2-1212",
            "prompt": (
                f"基於以下內容生成繁體中文摘要，不超過 100 字，使用完整、自然的句子和適當標點（句號、逗號等），"
                f"避免關鍵詞堆砌、破碎語句或虛構內容，直接總結，不含指令或非中文文字：\n\n{content}"
            ),
            "max_tokens": 150,
            "temperature": 0.5
        }
        response = session.post(endpoint, json=data, headers=headers, timeout=10)
        response.raise_for_status()
        summary = response.json()["choices"][0]["text"].strip()
        if len(summary) > 100:
            summary = summary[:99] + "。"
        time.sleep(1)
        return summary
    except Exception as e:
        logger.error(f"Grok API 摘要失敗: {e}")
        return "❌ 無法產生摘要"


def process_news(news_list):
    processed_news = []
    grouped_news = {}
    
    for news in news_list:
        title = translate_to_traditional_chinese(news["title"])
        summary = clean_text(news.get("summary", ""))
        if summary:
            summary = translate_to_traditional_chinese(summary)
        
        matched = False
        title_keywords = set(title.split())
        for key in grouped_news:
            key_keywords = set(key.split())
            similarity = SequenceMatcher(None, title, key).ratio()
            keyword_overlap = len(title_keywords & key_keywords) / max(len(title_keywords), len(key_keywords))
            if similarity > 0.8 or keyword_overlap > 0.6:
                grouped_news[key].append((news["source"], summary, news["link"]))
                matched = True
                break
        if not matched:
            grouped_news[title] = [(news["source"], summary, news["link"])]

    for title, news_group in grouped_news.items():
        if len(news_group) > 1:
            combined_content = "\n".join([item[1] for item in news_group if item[1]])
            summary = summarize_news(combined_content) if combined_content else "❌ 無摘要可用"
            processed_news.append({"source": "多來源統整", "title": title, "summary": summary, "link": news_group[0][2]})
        else:
            source, summary, link = news_group[0]
            summary = summarize_news(summary) if summary else "❌ 無摘要可用"
            processed_news.append({"source": source, "title": title, "summary": summary, "link": link})
    
    return processed_news[:15]  # 限制 10-15 則

def fetch_all_news():
    logger.info("開始抓取新聞...")
    news = []
    news.extend(fetch_rss_news())
    news.extend(fetch_html_news())
    news.extend(fetch_dynamic_news())
    logger.info(f"共抓取 {len(news)} 則新聞")
    
    if not news:
        logger.warning("無新聞可抓取")
        return []
    
    news = remove_duplicates(news)
    logger.info(f"去重後剩餘 {len(news)} 則新聞")
    
    logger.info("開始處理新聞...")
    processed_news = process_news(news)
    logger.info("新聞處理完成！")
    return processed_news

def send_news_to_line(user_id):
    """將新聞摘要推送至 LINE"""
    all_news = fetch_all_news()
    if not all_news:
        logger.warning("無新聞可推送")
        return

    message = "每日新聞摘要：\n\n"
    for item in all_news:
        message += f"[{item['source']}] {item['title']}\n{item['summary']}\n{item['link']}\n\n"
    
    messages = [message[i:i+2000] for i in range(0, len(message), 2000)] if len(message) > 2000 else [message]

    try:
        for msg in messages:
            push_request = PushMessageRequest(to=user_id, messages=[TextMessage(text=msg)])
            line_bot_api.push_message(push_request)
        logger.info(f"新聞已於 {time.strftime('%Y-%m-%d %H:%M:%S')} 推送至 LINE")
    except Exception as e:
        logger.error(f"推送至 LINE 失敗: {e}")

if __name__ == "__main__":
    USER_ID = "Ud1767205de40eb66f5b148ea4f9b79ee"
    send_news_to_line(USER_ID)
# -*- coding: utf-8 -*-
import feedparser
import requests
import logging
from bs4 import BeautifulSoup

# --- 0. 基礎設定 ---

# 設置日誌，方便觀察錯誤
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 【修正一】將 main.py 中的 RSS_FEEDS 字典複製過來
RSS_FEEDS = {
    "BBC": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "CNN": "http://rss.cnn.com/rss/edition_world.rss",
    "Fox News": "https://moxie.foxnews.com/google-publisher/world.xml"
}

# --- 1. 新聞爬取模組 (從 main.py 複製) ---

def fetch_rss_news():
    """從 RSS 來源抓取新聞"""
    all_news = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                if hasattr(entry, "title") and entry.title and hasattr(entry, "link") and entry.link:
                    # 注意：lxml 需要 `pip install lxml`
                    content = BeautifulSoup(getattr(entry, "summary", ""), "lxml").get_text(separator="\n", strip=True)
                    if len(content) < 50:
                        continue
                    all_news.append({
                        "source": source,
                        "title": entry.title,
                    })
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
        
        articles = soup.select('article.gc.u-clickable-card')[:10]
        for article in articles:
            title_tag = article.select_one('h3.gc__title a span')
            if title_tag:
                title = title_tag.get_text(strip=True)
                all_news.append({"source": "Al Jazeera", "title": title})

    except Exception as e:
        logger.error(f"抓取 Al Jazeera 新聞失敗: {e}")
    return all_news

# --- 2. 獨立測試區塊 ---

if __name__ == "__main__":
    print("="*20 + " 開始獨立測試爬蟲功能 " + "="*20)

    # --- 測試一：RSS 來源 ---
    print("\n--- 正在測試 RSS Feeds (BBC, CNN, Fox News)... ---")
    rss_results = fetch_rss_news()
    if rss_results:
        print(f"✅ 成功從 RSS 抓取到 {len(rss_results)} 則新聞：")
        # 建立一個字典來計算各來源的新聞數量
        source_counts = {}
        for item in rss_results:
            source = item['source']
            source_counts[source] = source_counts.get(source, 0) + 1
        # 印出統計結果
        for source, count in source_counts.items():
            print(f"  - {source}: {count} 則")
    else:
        print("❌ 從 RSS 來源沒有抓取到任何新聞！")

    print("\n" + "-"*50)

    # --- 測試二：Al Jazeera HTML 來源 ---
    print("\n--- 正在測試 Al Jazeera (HTML)... ---")
    html_results = fetch_html_news()
    if html_results:
        print(f"✅ 成功從 Al Jazeera 抓取到 {len(html_results)} 則新聞：")
        for i, item in enumerate(html_results):
            # 只印出前幾則作為範例
            if i < 5:
                print(f"  {i+1}. [{item['source']}] {item['title']}")
        if len(html_results) > 5:
            print("  ...")
    else:
        print("❌ 從 Al Jazeera 沒有抓取到任何新聞！")

    print("\n" + "="*22 + " 所有測試執行完畢 " + "="*22)
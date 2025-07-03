# -*- coding: utf-8 -*-

"""
壓力測試腳本：驗證 AI 能否根據【多個來源的完整內文】，
一次性生成一份融合後的、高品質的繁體中文標題和繁體中文摘要。
"""

import os
import re
from openai import OpenAI
from dotenv import load_dotenv

# --- 1. 初始化設定 ---
load_dotenv()
api_key = os.getenv("OPENROUTER_API_KEY")
if not api_key:
    print("錯誤：找不到 OPENROUTER_API_KEY。請檢查您的 .env 檔案。")
    exit()

try:
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )
    print("OpenRouter Client 初始化成功。")
except Exception as e:
    print(f"Client 初始化失敗: {e}")
    exit()

# --- 2. 準備【多來源的完整內文】輸入材料 ---
# 【升級】我們現在使用 'content' 來代表更長、更完整的文章內文
multi_source_news = [
    {
        "source_name": "AL JAZEERA",
        "title": "LIVE: Trump says Israel agrees to a Gaza truce, urges Hamas to accept deal",
        "content": "US President Donald Trump says Israel has agreed to “the necessary conditions to finalise” a 60-day ceasefire in Gaza, and urges Hamas to accept the proposal. Israeli forces have killed 109 Palestinians across Gaza, including 28 who were shot while waiting for food parcels at the US and Israeli-backed Gaza Humanitarian Foundation (GHF) sites. Officials at al-Shifa, the largest medical centre in northern Gaza, say hundreds of patients are “facing death” as the hospital runs out of fuel amid Israel’s blockade. Israel’s war on Gaza has killed at least 56,647 people and wounded 134,105, according to Gaza’s Health Ministry. An estimated 1,139 people were killed in Israel during the October 7 attacks, and more than 200 were taken captive."
    },
    {
        "source_name": "CNN",
        "title": "Trump says Israel has ‘agreed to the necessary conditions’ to finalize 60-day ceasefire in Gaza",
        "content": "President Donald Trump said Tuesday that Israel had “agreed to the necessary conditions” to finalize a ceasefire in Gaza, though it was not immediately clear whether Hamas would accept the terms. Two administration officials said Hamas still had to agree to the deal. In a post on Truth social, Trump said the Qataris and the Egyptians would deliver it. “My Representatives had a long and productive meeting with the Israelis today on Gaza,” Trump wrote. “Israel has agreed to the necessary conditions to finalize the 60 Day CEASEFIRE, during which time we will work with all parties to end the War. The Qataris and Egyptians, who have worked very hard to help bring Peace, will deliver this final proposal. I hope, for the good of the Middle East, that Hamas takes this Deal, because it will not get better — IT WILL ONLY GET WORSE. Thank you for your attention to this matter!” CNN reported earlier that Qatari officials had submitted to both Hamas and Israel on Tuesday a new proposal for a 60-day ceasefire, which is backed by the Trump administration, according to a source familiar with the matter. The proposal was finalized after months of behind-the-scenes efforts led by President Donald Trump’s special envoy Steve Witkoff, the source said. It was submitted on the same day that Israel’s Minister for Strategic Affairs Ron Dermer visited Washington for meetings with top Trump administration officials. The new proposal comes just days after Qatar helped broker a ceasefire between Iran and Israel after US and Israeli strikes on Iran’s nuclear program, and months after an initial Trump administration-backed ceasefire proposal for Gaza was rejected by Hamas."
    },
    {
        "source_name": "BBC",
        "title": "Israel has agreed to conditions for 60-day Gaza ceasefire, Trump says",
        "content": "Israel has agreed to the \"necessary conditions\" to finalise a 60-day ceasefire in Gaza, US President Donald Trump has said. During the proposed deal, \"we will work with all parties to end the War\", Trump said in a post on Truth Social, without detailing what the conditions are. \"The Qataris and Egyptians, who have worked very hard to help bring Peace, will deliver this final proposal. I hope... that Hamas takes this Deal, because it will not get better — IT WILL ONLY GET WORSE,\" Trump wrote. Israel launched a military campaign in Gaza after Hamas's 7 October, 2023 attack on Israel, in which around 1,200 people were killed. At least 56,647 have been killed in Gaza since then, according to the territory's Hamas-run health ministry. It was not immediately clear whether Hamas would accept the conditions of the ceasefire. Trump's announcement comes before a meeting with Israeli Prime Minister Benjamin Netanyahu scheduled for next week, in which the US president has said he would be \"very firm\". The US president said on Tuesday that he believed Netanyahu wanted to end hostilities in Gaza. \"He wants to. I can tell you he wants to. I think we'll have a deal next week,\" Trump added. On Tuesday, Israel's Strategic Affairs Minister Ron Dermer was due to meet US special envoy to the Middle East Steve Witkoff, US Secretary of State Marco Rubio and Vice President JD Vance in Washington. Last week, a senior Hamas official told the BBC mediators have increased efforts to broker a new ceasefire and hostage release deal in Gaza, but that negotiations with Israel remain stalled. Israel has said the conflict can only end when Hamas has been completely dismantled. Hamas has long called for a permanent truce and a complete Israeli withdrawal from Gaza."
    }
]


# --- 3. 全新設計的多來源融合 Prompt ---

def create_fusion_prompt(news_list: list) -> str:
    """動態生成包含所有新聞來源的 Prompt。"""
    
    input_materials_str = ""
    for i, news_item in enumerate(news_list):
        # 【升級】現在我們傳入 'content' 而不是 'summary'
        input_materials_str += f"""---
Source {i+1}: {news_item['source_name']}
Title: {news_item['title']}
Content: {news_item['content']}
"""
    
    prompt = f"""
# ROLE & GOAL (角色與目標)
You are a top-tier senior editor for a major Taiwanese news outlet, tasked with producing a fast, accurate, and purely objective news brief. Your work is for immediate publication.

# INPUT MATERIALS (輸入的多來源完整內文)
{input_materials_str.strip()}
---

# TASK (任務)
Your mission is to intelligently fuse all information from the provided texts. Identify the most critical facts, figures, and events to create an accurate overview. Your final output must be a single headline and a single summary paragraph in Traditional Chinese.

# OUTPUT REQUIREMENTS (產出要求)
1.  **Headline (繁體中文標題):** Must be a single, objective, and engaging headline that synthesizes the core event.
2.  **Summary Paragraph (繁體中文摘要):** Must be a single, cohesive paragraph between **80 and 180 characters**.
3.  **Quality (品質):**
    * Use natural, modern Taiwanese Mandarin. No awkward or overly emotional phrases.
    * No extra spaces between Chinese characters.
4.  **VOCABULARY (詞彙):**
    * **This is a non-negotiable rule:** All proper nouns must use standard Taiwanese translations.
    * **People:** `Trump` must be translated as `川普`.
    * **Places:** `Gaza` must be translated as `加薩`.

# FINAL OUTPUT FORMAT (請嚴格遵守此格式輸出)
繁體中文標題: [Your synthesized headline here]
繁體中文摘要: [Your synthesized summary paragraph here]
"""
    return prompt

# --- 4. 執行 AI 生成與結果解析 ---
print("\n--- 正在向 AI 發送多來源【全文】融合請求，請稍候... ---")
try:
    # 創建 Prompt
    fusion_prompt = create_fusion_prompt(multi_source_news)
    
    response = client.chat.completions.create(
        model="qwen/qwen-2-72b-instruct",
        messages=[{"role": "user", "content": fusion_prompt}],
        temperature=0.6,
    )
    ai_response_text = response.choices[0].message.content.strip()
    print("AI 回應接收成功！")

    # --- 步驟一：解析 AI 回應 ---
    print("\n--- 正在解析 AI 回應 ---")
    raw_title = "標題解析失敗"
    raw_summary = "摘要解析失敗"
    
    lines = ai_response_text.split('\n')
    for line in lines:
        if line.startswith("繁體中文標題:"):
            raw_title = line.replace("繁體中文標題:", "").strip()
        elif line.startswith("繁體中文摘要:"):
            raw_summary = line.replace("繁體中文摘要:", "").strip()
    
    # --- 步驟二：【新增】後處理區塊 ---
    print("\n--- 執行程式碼後處理 ---")
    
    # 1. 對標題進行後處理
    processed_title = raw_title.replace("特朗普", "川普").replace("加沙", "加薩")
    processed_title = re.sub(r'([一-龥])\s+([一-龥])', r'\1\2', processed_title)
    processed_title = processed_title.replace(' ', '')
    
    # 2. 對摘要進行後處理
    processed_summary = raw_summary.replace("特朗普", "川普").replace("加沙", "加薩")
    processed_summary = re.sub(r'([一-龥])\s+([一-龥])', r'\1\2', processed_summary)
    processed_summary = processed_summary.replace(' ', '')
    
    print("後處理完成！")
    # --- ----------------------- ---

    # --- 步驟三：呈現最終結果 ---
    print("\n" + "="*40)
    print("✅【多來源全文融合生成結果】")
    # 【修正】改為印出經過後處理的 `processed_` 變數
    print(f"標題：{processed_title}")
    print(f"摘要：{processed_summary}")
    print(f"摘要字數：{len(processed_summary)}")
    print("="*40)

except Exception as e:
    print(f"\nAPI 呼叫失敗: {e}")
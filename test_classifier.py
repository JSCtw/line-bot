# -*- coding: utf-8 -*-

"""
測試腳本：驗證 Mistral 7B Instruct 模型能否準確地
對新聞進行「主題」與「影響力範疇」的二維分類。
"""

import os
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

# --- 2. 準備多場景測試案例 ---
test_cases = [
    {
        "case_name": "【關鍵測試】有全球影響的美國法案",
        "title": "US Senate passes major economic bill with global trade implications",
        "content": "The U.S. Senate passed a sweeping economic package aimed at boosting domestic manufacturing. The bill includes provisions that could significantly alter international supply chains for semiconductors and electric vehicles, raising concerns among trade partners in Asia and Europe about new tariffs."
    },
    {
        "case_name": "【清晰案例】地緣政治衝突",
        "title": "Israel agrees to 60-day Gaza ceasefire conditions, Trump says",
        "content": "US President Donald Trump announced that Israel has agreed to the necessary conditions for a 60-day ceasefire in Gaza. The deal, mediated by Qatar and Egypt, is now pending Hamas's response. The conflict has led to a major humanitarian crisis in the region."
    },
    {
        "case_name": "【清晰案例】全球財經",
        "title": "Federal Reserve signals potential interest rate hike amid inflation fears",
        "content": "The US Federal Reserve hinted at another potential interest rate hike to combat persistent inflation. This move is expected to impact global financial markets, causing volatility in stock exchanges from Tokyo to London as investors anticipate tighter monetary policy."
    },
    {
        "case_name": "【清晰案例】區域性政治",
        "title": "European Union announces new data privacy regulations for all member states",
        "content": "The European Commission has unveiled a new set of data privacy laws, known as GDPR II, which will apply to all 27 member states. The regulation aims to give users more control over their personal data and will affect how tech companies operate across Europe."
    },
    {
        "case_name": "【排除測試】純國內社會新聞",
        "title": "Jury selection begins in high-profile Diddy sex trafficking trial in New York",
        "content": "Jury selection has started in the highly anticipated sex trafficking trial of music mogul Sean 'Diddy' Combs. The trial, taking place in a New York federal court, is focused on allegations from multiple plaintiffs and does not involve international law."
    },
    {
        "case_name": "【排除測試】純國內財經新聞",
        "title": "IRS reminds US citizens of upcoming tax filing deadline",
        "content": "The Internal Revenue Service (IRS) has issued a public reminder for all U.S. citizens and residents that the deadline for filing federal income tax returns is approaching. This notice pertains only to U.S. tax obligations."
    }
]

# --- 3. 全新設計的二維分類 Prompt ---

def create_classifier_prompt(title: str, content: str) -> str:
    """為分類任務創建專屬的 Prompt。"""
    
    prompt = f"""
# ROLE & GOAL
You are an expert news classification engine. Your task is to analyze the provided news article and assign two labels to it: a "Topic" label and a "Scope of Impact" label.

# INPUT
* **Title:** "{title}"
* **Content:** "{content}"

# INSTRUCTIONS
1.  **Analyze the Topic:** First, determine the primary subject matter and classify it into ONE of the following categories:
    * `政治 & 外交 (Politics & Diplomacy)`
    * `經濟 & 金融 (Economy & Finance)`
    * `軍事 & 衝突 (Military & Conflict)`
    * `科技 & 產業 (Technology & Industry)`
    * `社會 & 人文 (Society & Culture)`
    * `災害 & 環境 (Disaster & Environment)`
    * `其他 (Other)`

2.  **Analyze the Scope of Impact:** Second, determine the scale of the event's impact and classify it into ONE of the following categories:
    * **`全球性 (Global)`:** The event's consequences significantly affect multiple continents or global systems (finance, supply chains, etc.).
    * **`區域性 (Regional)`:** The event's primary impact is on a specific region (e.g., Europe, Middle East, Asia-Pacific).
    * **`國內性 (Domestic)`:** The event's primary impact is confined within a single country.

# OUTPUT FORMAT
You MUST provide the output in two separate lines, exactly as follows:
Topic: [Your chosen topic classification here]
Scope: [Your chosen scope classification here]
"""
    return prompt

# --- 4. 執行測試迴圈 ---
if __name__ == "__main__":
    print("="*20 + " 開始測試 AI 新聞分類器 " + "="*20)
    
    for case in test_cases:
        print(f"\n--- 正在測試案例: {case['case_name']} ---")
        print(f"新聞標題: {case['title']}")
        
        try:
            classifier_prompt = create_classifier_prompt(case['title'], case['content'])
            
            response = client.chat.completions.create(
                model="mistralai/mistral-7b-instruct", # 使用我們選定的輕量級模型
                messages=[{"role": "user", "content": classifier_prompt}],
                temperature=0.1, # 分類任務需要精準，使用低溫
            )
            ai_response_text = response.choices[0].message.content.strip()
            
            # 解析 AI 回應
            topic = "解析失敗"
            scope = "解析失敗"
            for line in ai_response_text.split('\n'):
                if line.startswith("Topic:"):
                    topic = line.replace("Topic:", "").strip()
                elif line.startswith("Scope:"):
                    scope = line.replace("Scope:", "").strip()
            
            print(f"✅ AI 分類結果 -> 主題: {topic} | 影響力: {scope}")
            
        except Exception as e:
            print(f"❌ 案例執行失敗: {e}")
    
    print("\n" + "="*22 + " 所有測試執行完畢 " + "="*22)
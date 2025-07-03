# -*- coding: utf-8 -*-

"""
新聞處理管線測試腳本 (News Processing Pipeline Test Script)
功能：
1. 讀取兩篇來自不同來源、關於同一事件的英文新聞。
2. 【步驟一】使用 Meta Llama 4 模型將兩篇新聞融合成一篇客觀的英文摘要。
3. 【步驟二】使用 Qwen 模型將英文摘要翻譯成符合台灣用語習慣的繁體中文摘要。
4. 輸出每一步的結果以供驗證。
執行前請確保：
- 已安裝 openai 和 python-dotenv 套件。
- 專案根目錄下有 .env 檔案，並已設定 OPENROUTER_API_KEY。
"""

import os
import re
from openai import OpenAI
from dotenv import load_dotenv

# --- 1. 初始化設定 ---

# 載入 .env 檔案中的環境變數
load_dotenv()

# 檢查 API 金鑰是否存在
api_key = os.getenv("OPENROUTER_API_KEY")
if not api_key:
    print("錯誤：找不到 OPENROUTER_API_KEY。請檢查您的 .env 檔案。")
    exit()

# 設定 OpenRouter Client
try:
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )
    print("OpenRouter Client 初始化成功。")
except Exception as e:
    print(f"Client 初始化失敗: {e}")
    exit()

# --- 2. 測試用的新聞內容 ---
news_source_A = """
(CNN) -A marathon voting session on President Donald Trump’s sweeping domestic policy bill is underway in the Senate and has stretched overnight into the early hours of Tuesday morning after a weekend of negotiations and delays.
The vote-a-rama – an open-ended, hourslong series of votes on amendments, some political, some substantive – started around 9:35 a.m. on Monday and is still going with no end in sight. The extended voting session provides an opportunity for Republicans to make any eleventh-hour adjustments to the package and Democrats to push on GOP weak points in the bill and put their colleagues on the spot. Those politically tough votes are likely to provide fodder for campaign ads down the line.
Senate Majority Leader John Thune told reporters around 1 a.m. on Tuesday that “progress is a very elusive term” when asked if lawmakers are making progress toward a final vote.
Trump’s multitrillion-dollar bill would lower federal taxes and infuse more money into the Pentagon and border security agencies, while downsizing government safety-net programs including Medicaid.
Democrats have zeroed in on Medicaid and other safety-net programs, such as food stamps, as they message against the president’s agenda. The vote-a-rama comes after Senate Democrats employed a major delay tactic over the weekend that forced clerks to spend more than a dozen hours reading aloud the entire bill.
Lawmakers are up against an extremely tight timeline to pass the legislation. The president has demanded Congress deliver the bill to his desk by the Fourth of July, but the measure must still go back to the House if it passes the Senate.
In the House, Speaker Mike Johnson is confronting growing levels of consternation in his ranks about the final product, raising questions about that measure’s fate in his chamber.
Historic cuts to Medicaid
A number of Republicans in each chamber are closely watching any changes made to Medicaid provisions in the bill.
In the Senate, Alaska Republican Sen. Lisa Murkowski – who GOP leadership had to convince to advance the legislation over the weekend – crossed the aisle to vote with Democrats on several amendments affecting the bill’s SNAP and Medicaid provisions, as well as to shore up support for rural hospitals. The proposed changes were ultimately unsuccessful, but underscore the flashpoints within the Republican Party.
GOP Sen. Susan Collins of Maine offered an amendment during the vote-a-rama aimed at raising more money for rural health care providers, a move that comes as changes to Medicaid’s provider taxes in the bill have been contentious for the GOP.
The funds for this would come from increasing taxes on those who make more than $25 million annually, or couples who make more than $50 million. The Senate ultimately took a procedural vote on the amendment, rather than voting on the amendment itself, and it failed to advance.
Asked if she was disappointed by the outcome of the vote, Collins said “I was surprised at the hypocrisy of the Democrats on it. Had they voted for it, it would have passed easily. So that was a surprise.”
However, Collins maintained that her amendment’s failure to advance “has absolutely no impact on my vote on final passage.”
“We’ll see what the final bill looks like,” she added. “I’m not going to announce that prematurely.”
The Senate version of the megabill would leave 11.8 million more people without health insurance in 2034, according to a Congressional Budget Office analysis released over the weekend. That’s more than the 10.9 million more people projected to be left uninsured by the House-passed version of the bill.
Both chambers are calling for historic spending cuts to Medicaid, which provides coverage to more than 71 million low-income Americans, including children, senior citizens, people with disabilities and other adults. The package would also enact changes to the Affordable Care Act that are projected to reduce enrollment in the landmark health reform law that Trump and Republicans have long sought to dismantle.
But the Senate version calls for even deeper cuts to the Medicaid, leading to the larger estimate.
It would slash federal support for Medicaid by $930 billion over a decade, Sen. Ron Wyden, the top Democrat on the Senate Finance Committee, said over the weekend, citing a CBO estimate. The House version is projected to reduce federal spending on the program by about $800 billion, according to the CBO.
Both chambers would require certain able-bodied adults ages 19-64 to work to maintain their Medicaid benefits for the first time in the program’s 60-year history. But the Senate version would impose the work requirement on parents of children ages 14 and older, while the House version would exempt parents of dependent children.
The Senate version would also lower the cap on the taxes that states levy on health care providers to help fund the program and increase reimbursement rates for providers. However, that provision would apply only to the 40 states and the District of Columbia that have expanded Medicaid to low-income adults. The House bill would put a moratorium on the states’ existing provider taxes.
Increase to the deficit
The first vote taken by senators Monday dealt with a procedural argument over the so-called current policy baseline and how to calculate the costs of the bill. While it may seem dry, Republicans’ use of current policy baseline in their calculations will set a precedent allowing both parties to be much more generous when calculating costs of tax bills going forward.
Trump and some GOP leaders, including Senate Finance Chairman Mike Crapo, pushed the alternative “current policy baseline” scoring method, which seemingly greatly minimizes the deficit impact of the bill because it would not include the cost of extending the expiring 2017 tax provisions.
The CBO, however, calculated the cost of the bill using its traditional scoring method, known as “current law baseline,” which assumed the expiring provisions of the 2017 Trump tax cuts lapse as scheduled at the end of the year.
It projected the Senate’s bill would also cost far more than the House-approved bill, adding nearly $3.3 trillion to the deficit over a decade.
The Senate version is costlier in large part because it contains bigger tax cuts, while shrinking some of the spending cuts and revenue raisers, said Marc Goldwein, senior policy director at the Committee for a Responsible Federal Budget, a watchdog group.
For instance, the Senate bill would make permanent three corporate tax breaks that were part of the 2017 law and would lessen the cuts to the food stamp program.
“They expand the giveaways and shrink the takeaways,” Goldwein told CNN.
Using the current policy baseline, the Senate version would cost roughly $508 billion over the next decade, according to a separate CBO estimate released Saturday night.
This story has been updated with additional developments.
"""

news_source_B = """
(BBC) - "The thing that [Scott's] bill doesn't do is it doesn't take effect until 2031. So I'm not sure how you can make the argument that it's going to kick any people off of health insurance tomorrow," Senate Majority Leader John Thune said.
Democrats, who have repeatedly denounced the bill, particularly for cutting health insurance for millions of poorer Americans, are expected to use all 10 of their allotted hours of debate, while Republicans probably won't.
Democrat Senator Adam Schiff called the bill "terrible" and told the BBC he was unsure if Senate Republicans would meet Trump's Friday deadline.
Press Secretary Karoline Leavitt said Trump is "confident" the bill would be passed and still expects it on his desk by 4 July.

On Sunday, Democrats used a political manoeuvre to stall the bill's progress, calling on Senate clerks to read all 940 pages of the bill aloud, a process that took 16 hours.
It followed weeks of public discussion and the Senate narrowly moving on the budget bill in a 51-49 vote over the weekend.
Two Republicans sided with Democrats in voting against opening debate, arguing for further changes to the legislation.
One of those Republicans, North Carolina Senator Thom Tillis, announced his retirement following that vote and said the legislation broke promises that Trump and Republicans made to voters.
"Too many elected officials are motivated by pure raw politics who really don't give a damn about the people they promised to represent on the campaign trail," Tillis wrote in his announcement.
The White House reacted angrily to Tillis' comments, with Leavitt saying Tillis was "just wrong".
Kentucky Republican Senator Rand Paul objected to the debt increase, and cuts to Medicaid.
A look at the key items in Trump's 'big, beautiful bill'
The woman who could bust Trump's 'big beautiful bill
During the full Senate vote on the bill - expected early Tuesday morning - Republicans can only afford three defections in order for the bill to pass.
If they lose three votes, Vice-President JD Vance will have to cast a tie-breaking vote.
The bill would then return to the House of Representatives, where leadership has advised a full vote on the Senate's bill could come as early as Wednesday morning.
Fiscal hawks of the Republican-led House Freedom Caucus have threatened to torpedo the Senate version over budget disagreements.
The Senate proposal adds over $650bn to the national deficit, the group said in a post on social media on Monday.
"That's not fiscal responsibility," they said. "It's not what we agreed to."
Democrats in both chambers have largely objected to the spending cuts and the proposed extension of tax breaks.
Meanwhile, Republican debate has focused on how much to cut welfare programmes in order to extend $3.8tn (£2.8tn) in Trump tax breaks.
Proposed cuts could strip nearly 12 million Americans of their health insurance coverage and add $3.3tn (£2.4tn) in debt, according to the Congressional Budget Office, a non-partisan federal agency.
"""

# --- 3. 鏈式 Prompt 執行函式 ---

def synthesize_english_summary(article_a: str, article_b: str) -> str:
    """
    步驟一：融合新聞並生成客觀的英文摘要。
    使用高推理能力的模型 (Llama 4)。
    """
    print("\n--- [步驟一] 開始：融合新聞並生成英文摘要 ---")
    
    prompt = f"""
    You are a factual news synthesizer. Your task is to read the following two news articles about the same event and synthesize them into a single, objective, and concise summary in English.
    Focus only on the core facts and key information presented in both articles, such as decisions made, key figures, and outlooks. Ignore any redundant phrasing or minor details.

    Article 1:
    {article_a}

    Article 2:
    {article_b}

    Synthesized English Summary:
    """
    
    try:
        response = client.chat.completions.create(
            model="meta-llama/llama-4-maverick",
            messages=[
                {"role": "system", "content": "You are a helpful assistant designed to synthesize news."},
                {"role": "user", "content": prompt},
            ]
        )
        summary = response.choices[0].message.content
        print("英文摘要生成成功！")
        return summary
    except Exception as e:
        print(f"步驟一 API 呼叫失敗: {e}")
        return ""

def translate_and_style_summary(english_summary: str) -> str:
    """
    步驟二：將英文摘要翻譯、風格化並縮短為繁體中文摘要。
    使用中文能力強的模型 (Qwen)，並嚴格遵循風格指南。
    """
    print("\n--- [步驟二] 開始：翻譯、風格化並生成最終繁中摘要 ---")
    
    prompt = f"""
# ROLE & GOAL (角色與目標)
You are a senior editor for a major Taiwanese online news service (e.g., LINE TODAY, ETtoday), crafting the lead summary for a top story. Your writing must be sharp, clear, and reflect the style of modern Taiwanese journalism.

# CRITICAL INSTRUCTIONS (核心指令)

1.  **FINAL FORMAT (最終格式):** A single, powerful paragraph.

2.  **LENGTH (長度):** The final paragraph MUST be **between 100 and 150 characters**. This length provides enough detail without overwhelming the reader on a mobile screen.

3.  **LANGUAGE STYLE (語言風格):**
    * Use natural, modern Taiwanese Mandarin. **Your writing should flow smoothly.** For example, instead of a stiff phrase like "致...人失保", use a more narrative form like "恐將導致...人失去健保".
    * **(新增) You are encouraged to use vivid, commonly-used metaphors or terms seen in Taiwanese online media to make the summary more engaging.**

4.  **VOCABULARY & NAMES (詞彙與人名):**
    * **Non-negotiable:** `Trump` must be translated as `川普`.
    * **(新增) Context for Names:** When mentioning a non-president foreign individual for the first time (e.g., McConnell), provide their title or affiliation for context (e.g., "共和黨參議員麥康諾").

---
**English Summary to Process:**
"{english_summary}"
---

**Your Final, Polished News Summary (100-150 characters):**
"""
    
    try:
        response = client.chat.completions.create(
            model="qwen/qwen-2-72b-instruct",
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
        )
        final_summary = response.choices[0].message.content.strip()
        print("最終繁中摘要生成成功！")
        return final_summary
    except Exception as e:
        print(f"步驟二 API 呼叫失敗: {e}")
        return "❌ 無法產生摘要"

# --- 4. 主執行流程 ---
if __name__ == "__main__":
    print("="*40)
    print("啟動新聞摘要生成管線...")
    print("="*40)

    # 執行步驟一
    intermediate_summary = synthesize_english_summary(news_source_A, news_source_B)
    
    if intermediate_summary:
        final_result_from_ai = translate_and_style_summary(intermediate_summary)
        
        # --- 【強化版】程式碼後處理區塊 ---
        print("\n--- 執行程式碼後處理 ---")
        
        # 1. 修正頑固的譯名錯誤 (最終保險)
        processed_result = final_result_from_ai.replace("特朗普", "川普")
        
        # 2. 移除中文字元間的多餘空格
        processed_result = re.sub(r'([一-龥])\s+([一-龥])', r'\1\2', processed_result)
        
        # 3. 【新增】移除所有剩餘的半形空格，作為最終清理
        processed_result = processed_result.replace(' ', '')
        
        print("後處理完成！")
        # --- ----------------------- ---

        print("\n" + "="*40)
        print("✅【最終生成結果】")
        print(processed_result) # <-- 列印經過後處理的最終結果
        print(f"摘要字數: {len(processed_result)}")
        print("="*40)
        
        # 檢查是否符合長度要求 (使用我們更新後的標準)
        if len(processed_result) > 180:
            print(f"\n⚠️ 警告：摘要長度 ({len(processed_result)}字) 超過 180字上限，可能需要調整 Prompt。")
        elif len(processed_result) < 80:
            print(f"\n⚠️ 警告：摘要長度 ({len(processed_result)}字) 低於 80字下限，可能過於精簡。")
        elif processed_result == "❌ 無法產生摘要":
            print("\n🚨 錯誤：摘要生成失敗，請檢查 API 錯誤訊息。")
        else:
            print("\n👍 摘要長度符合要求，管線測試成功！")
            
    else:
        print("\n🚨 錯誤：因英文摘要生成失敗，管線已中止。")
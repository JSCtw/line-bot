# LINE-BOT-V3：AI 國際新聞推播機器人

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109.0-009688.svg)](https://fastapi.tiangolo.com/)
[![Deploy](https://img.shields.io/badge/Deploy-GCP_Cloud_Run-4285F4.svg)](https://cloud.google.com/run)

這是一個部署於 GCP Cloud Run 的高可用性 LINE Bot 專案。系統主要負責透過 webhook 接收觸發請求、執行自動化新聞抓取與資料清理（RSS/HTML），並整合 OpenRouter 進行 AI 分類、摘要與翻譯，最後將精選的新聞以 Flex Message 形式推播至 LINE 用戶端，同時結合 Google Sheets 進行全流程的資料記錄與狀態追蹤。

## ✨ 核心功能 (Core Features)

- **多來源自動化抓取與正規化**：支援多節點 RSS 與 HTML 來源抓取。內建 `NewsNormalizer` 進行深度文字清理、控制字符移除、以及 UTC 時間標準化與時效過濾。
- **智能去重 (Deduplication)**：透過 `SequenceMatcher` 進行標題相似度比對（閾值 0.8），自動合併跨媒體的重複報導，確保資訊精煉。
- **AI 分類與在地化摘要**：
  - **分類**：採用 `mistralai/mistral-7b-instruct` 進行精確的主題與影響範圍標籤化。
  - **摘要與翻譯**：利用 `google/gemma-3-12b-it:free` 生成流暢的繁體中文摘要，並嚴格遵循 Google Sheets 中設定的專有詞彙表 (Glossary) 進行強制翻譯替換。
- **LINE Flex Message 推播**：自動將處理後的新聞組裝為高質感的 Carousel Flex Message 輪播卡片推播給訂閱用戶。
- **企業級韌性與降級策略**：針對外部 API (OpenRouter, LINE, Google Sheets) 實作基於 Tenacity 的重試矩陣，涵蓋 429 Rate Limit 處理；並具備零新聞不推播、API 異常時的 Discord 自動警報機制。

## 📂 專案結構 (Project Structure)

```text
line-bot/
├── core/                   # 核心商業邏輯
│   ├── news_fetcher.py     # 新聞抓取模組 (整合 Normalizer)
│   ├── news_classifier.py  # AI 內容分類模組
│   ├── news_processor.py   # 新聞後處理、去重與摘要生成
│   ├── line_notifier.py    # LINE Flex Message 構建與推播
│   └── sheet_manager.py    # Google Sheets 讀寫與歷史紀錄管理
├── utils/                  # 共用工具函式
│   ├── news_normalizer.py  # [v4.1] 文字清理與時區處理
│   ├── retry_policy.py     # Tenacity 重試與錯誤處理策略
│   ├── http_client.py      # 非同步 HTTP 客戶端
│   ├── config_manager.py   # 配置檔讀取
│   └── logger.py           # 結構化日誌 (Structured Logging)
├── tests/                  # Pytest 測試案例 (涵蓋率要求: core ≥ 90%, utils ≥ 80%)
├── main.py                 # FastAPI 應用程式入口
├── config.yaml             # 專案環境與系統設定 (包含 normalizer 參數)
├── .env.example            # 本機開發環境變數範本
├── Dockerfile              # 容器建置設定 (使用純 Uvicorn 部署)
├── cloudbuild.yaml         # GCP Cloud Build 自動化部署流程
├── CLAUDE.md               # Claude Code 專案導讀指南
├── gcp.md                  # GCP 部署與雲端架構設定說明
└── spec.md                 # 詳細功能規格與流程文件 (SPEC v4.1)
```

## 🚀 本機開發與執行 (Local Development)

### 1. 環境準備

建議使用 Python 3.11+ 進行開發。首先建立並啟動虛擬環境：

```bash
python -m venv venv
source venv/bin/activate  # Windows 請使用 venv\Scripts ctivate
```

### 2. 安裝依賴

安裝主要執行套件與開發測試工具：

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 3. 設定環境變數

複製環境變數範本，並填寫本機測試所需的金鑰資訊：

```bash
cp .env.example .env
```

_(請確保 `.env` 檔案內包含 `OPENROUTER_API_KEY`, `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, `GOOGLE_SHEET_URL` 等必填項目)_

### 4. 啟動服務

使用 Uvicorn 啟動 FastAPI 服務，支援熱重載 (Hot Reload)：

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

服務啟動後，可透過 ngrok 進行本機 Webhook 測試。

### 5. 執行測試

本專案嚴格控管程式碼品質，提交前請確認測試通過：

```bash
pytest tests/ -v
pytest --cov=core --cov=utils --cov-report=term-missing tests/
```

## ☁️ 部署 (Deployment)

本專案採用無伺服器架構，以 Docker 容器方式部署至 **GCP Cloud Run**。
在 `v4.1` 架構中，為優化 Cloud Run (Concurrency=1) 的執行效率，已移除 Gunicorn，改採輕量化的單一 Uvicorn 啟動模式，有效降低記憶體開銷與冷啟動延遲。

### 使用 Cloud Build 進行部署：

```bash
gcloud builds submit --config=cloudbuild.yaml --substitutions=_SERVICE_NAME=line-news-bot
```

詳細的雲端環境建置、Service Account 權限設定（Sheets API, Drive API），請參閱文件 [gcp.md](./gcp.md)。

## 📚 文件導覽 (Documentation)

專案包含詳盡的規格與指引文件，開發前請務必參閱：

- [spec.md](./spec.md)：系統功能規格書 (v4.1)，包含架構演進、重試矩陣設計、AI Prompt Templates 與邊界情境定義。
- [gcp.md](./gcp.md)：雲端架構設計與基礎設施部署指南。
- [CLAUDE.md](./CLAUDE.md)：專門為 AI 輔助開發工具 (如 Claude Code) 準備的專案理解導引。

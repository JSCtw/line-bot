#core/SheetManager.py
# -*- coding: utf-8 -*-
"""
Google Sheets 管理器
負責 Google Sheets 的所有操作，包含術語表載入與發送記錄管理
"""

import asyncio
import logging
import os
import json  # ❗️【新增】匯入 json 模組
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Set, Optional, Any
from concurrent.futures import ThreadPoolExecutor

import gspread
from google.oauth2.service_account import Credentials
from google.auth.exceptions import DefaultCredentialsError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

class SheetManager:
    """Google Sheets 管理器"""
    
    # 保持 __init__ 不變
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.sheets_config = config.get('google_sheets', {})
        self.timezone = timezone(timedelta(hours=8))  # 台灣時區
        
        # Google Sheets 相關屬性
        self.client: Optional[gspread.Client] = None
        self.spreadsheet: Optional[gspread.Spreadsheet] = None
        self.worksheets: Dict[str, gspread.Worksheet] = {}
        
        # 快取
        self._glossary_cache: Optional[Dict[str, str]] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = 3600  # 1小時快取
        
        # 線程池 (用於同步轉異步)
        self.executor = ThreadPoolExecutor(max_workers=3)
    
    # 保持 initialize 不變
    async def initialize(self) -> None:
        """初始化 Google Sheets 連線"""
        logger.info("🔗 初始化 Google Sheets 連線...")
        
        # 在線程池中執行同步初始化
        await asyncio.get_event_loop().run_in_executor(
            self.executor, self._sync_initialize
        )
        
        logger.info("✅ Google Sheets 初始化完成")
    
    # ❗️【修改】替換 _sync_initialize 的內部邏輯
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((DefaultCredentialsError, gspread.exceptions.APIError, FileNotFoundError))
    )
    def _sync_initialize(self) -> None:
        """
        同步的初始化邏輯。
        優先從 'GOOGLE_CREDENTIALS_JSON' 環境變數讀取憑證 (用於 Zeabur)。
        若失敗，則回退到本地 'gcp_credentials.json' 檔案 (用於本地開發)。
        """
        try:
            creds: Optional[Credentials] = None
            scopes = ['https://www.googleapis.com/auth/spreadsheets']
            
            # 1. 嘗試從環境變數讀取 (Zeabur / 生產環境)
            creds_json_str = os.getenv('GOOGLE_CREDENTIALS_JSON')
            
            if creds_json_str:
                logger.info("偵測到 'GOOGLE_CREDENTIALS_JSON' 環境變數，將使用此憑證。")
                try:
                    creds_dict = json.loads(creds_json_str)
                    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
                except json.JSONDecodeError:
                    logger.error("無法解析 GOOGLE_CREDENTIALS_JSON，請檢查環境變數的 JSON 格式。")
                    raise  # 格式錯誤，重試也無用，直接拋出
                except Exception as e:
                    logger.error(f"從環境變數載入憑證失敗: {e}")
                    raise # 拋出以觸發 tenacity 重試
            
            else:
                # 2. 回退到本地檔案 (本地開發)
                logger.info("未偵測到 'GOOGLE_CREDENTIALS_JSON'，將回退使用本地憑證檔案 'gcp_credentials.json'。")
                
                # 使用您原本的路徑邏輯
                creds_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'gcp_credentials.json')
                
                if not os.path.exists(creds_path):
                    logger.error(f"本地憑證檔案 'gcp_credentials.json' 不存在於: {creds_path}")
                    raise FileNotFoundError(f"本地憑證檔案不存在: {creds_path}")
                
                creds = Credentials.from_service_account_file(creds_path, scopes=scopes)

            # 3. 檢查憑證是否成功載入
            if not creds:
                # 理論上，如果 creds_json_str 和本地檔案都不存在，會在上面拋出錯誤
                raise ValueError("無法獲取 Google 憑證 (環境變數和本地檔案均失敗)。")

            # 4. 授權 gspread client (保持不變)
            self.client = gspread.authorize(creds)
            
            # 5. 開啟試算表 (保持不變)
            sheet_url = os.getenv("GOOGLE_SHEET_URL")
            if not sheet_url:
                raise ValueError("GOOGLE_SHEET_URL 環境變數未設定")
            
            self.spreadsheet = self.client.open_by_url(sheet_url)
            logger.info(f"📊 成功開啟試算表: {self.spreadsheet.title}")
            
            # 6. 初始化工作表 (保持不變)
            self._initialize_worksheets()
            
        except Exception as e:
            logger.error(f"Google Sheets 初始化失敗: {e}")
            raise  # 重新拋出錯誤，以便 tenacity 捕捉
    
    # --- 以下所有方法保持不變 ---

    def _initialize_worksheets(self) -> None:
        """初始化所需的工作表"""
        worksheet_names = self.sheets_config.get('worksheets', {})
        
        for key, sheet_name in worksheet_names.items():
            try:
                worksheet = self.spreadsheet.worksheet(sheet_name)
                self.worksheets[key] = worksheet
                logger.info(f"✅ 找到工作表: {sheet_name}")
            except gspread.exceptions.WorksheetNotFound:
                logger.warning(f"⚠️ 工作表 '{sheet_name}' 不存在，正在建立...")
                worksheet = self._create_worksheet(key, sheet_name)
                self.worksheets[key] = worksheet
                logger.info(f"✅ 已建立工作表: {sheet_name}")
    
    def _create_worksheet(self, key: str, sheet_name: str) -> gspread.Worksheet:
        """建立新工作表並設定表頭"""
        if key == 'sent_news_log':
            worksheet = self.spreadsheet.add_worksheet(
                title=sheet_name, rows=1000, cols=4
            )
            worksheet.update('A1:D1', [['Timestamp', 'Title', 'Summary', 'Link']])
            
        elif key == 'glossary':
            worksheet = self.spreadsheet.add_worksheet(
                title=sheet_name, rows=1000, cols=2
            )
            columns = self.sheets_config.get('glossary_columns', {})
            headers = [
                columns.get('english_term', 'English_Term'),
                columns.get('taiwan_term', 'Taiwan_Term')
            ]
            worksheet.update('A1:B1', [headers])
            
            # 添加一些範例資料
            sample_data = [
                ['Trump', '川普'],
                ['Gaza', '加薩'],
                ['Netanyahu', '納坦雅胡'],
                ['Khamenei', '哈米尼']
            ]
            worksheet.update('A2:B5', sample_data)
            logger.info(f"📝 已在術語表中添加 {len(sample_data)} 條範例資料")
            
        else:
            worksheet = self.spreadsheet.add_worksheet(
                title=sheet_name, rows=1000, cols=10
            )
        
        return worksheet
    
    async def load_glossary(self) -> Dict[str, str]:
        """載入術語表，包含快取機制"""
        # 檢查快取
        if self._is_cache_valid():
            logger.info("📚 使用快取的術語表")
            return self._glossary_cache.copy()
        
        logger.info("🔄 重新載入術語表...")
        
        # 在線程池中載入術語表
        glossary = await asyncio.get_event_loop().run_in_executor(
            self.executor, self._sync_load_glossary
        )
        
        # 更新快取
        self._glossary_cache = glossary
        self._cache_time = datetime.now()
        
        logger.info(f"✅ 術語表載入完成: {len(glossary)} 條術語")
        return glossary.copy()
    
    def _is_cache_valid(self) -> bool:
        """檢查快取是否有效"""
        if not self._glossary_cache or not self._cache_time:
            return False
        
        elapsed = (datetime.now() - self._cache_time).total_seconds()
        return elapsed < self._cache_ttl
    
    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=5),
        retry=retry_if_exception_type((Exception,))
    )
    def _sync_load_glossary(self) -> Dict[str, str]:
        """同步載入術語表"""
        try:
            if 'glossary' not in self.worksheets:
                logger.warning("⚠️ 術語表工作表不存在，返回空字典")
                return {}
            
            worksheet = self.worksheets['glossary']
            
            # 獲取所有資料
            records = worksheet.get_all_records()
            
            # 轉換為字典格式
            glossary = {}
            columns = self.sheets_config.get('glossary_columns', {})
            english_col = columns.get('english_term', 'English_Term')
            taiwan_col = columns.get('taiwan_term', 'Taiwan_Term')
            
            for record in records:
                english_term = record.get(english_col, '').strip()
                taiwan_term = record.get(taiwan_col, '').strip()
                
                if english_term and taiwan_term:
                    glossary[english_term] = taiwan_term
            
            # 合併預設翻譯
            default_translations = self.config.get('default_translations', {})
            glossary.update(default_translations)
            
            return glossary
            
        except Exception as e:
            logger.error(f"載入術語表失敗: {e}")
            # 返回預設翻譯作為後備
            return self.config.get('default_translations', {})
    
    async def get_sent_links(self) -> Set[str]:
        """獲取已發送的新聞連結集合"""
        logger.info("📋 載入已發送新聞記錄...")
        
        links = await asyncio.get_event_loop().run_in_executor(
            self.executor, self._sync_get_sent_links
        )
        
        logger.info(f"✅ 載入 {len(links)} 條已發送記錄")
        return links
    
    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=5),
        retry=retry_if_exception_type((Exception,))
    )
    def _sync_get_sent_links(self) -> Set[str]:
        """同步獲取已發送連結"""
        try:
            if 'sent_news_log' not in self.worksheets:
                logger.warning("⚠️ 已發送記錄工作表不存在")
                return set()
            
            worksheet = self.worksheets['sent_news_log']
            
            # 獲取 Link 欄位 (第D欄)
            links = worksheet.col_values(4)
            
            # 移除標題列，轉換為集合
            return set(links[1:]) if len(links) > 1 else set()
            
        except Exception as e:
            logger.error(f"讀取已發送記錄失敗: {e}")
            return set()
    
    async def log_sent_news(self, news_items: List[Dict[str, str]]) -> None:
        """記錄已發送的新聞到工作表"""
        if not news_items:
            return
        
        logger.info(f"💾 記錄 {len(news_items)} 則新聞到 Google Sheets...")
        
        await asyncio.get_event_loop().run_in_executor(
            self.executor, self._sync_log_sent_news, news_items
        )
        
        logger.info("✅ 新聞記錄完成")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((Exception,))
    )
    def _sync_log_sent_news(self, news_items: List[Dict[str, str]]) -> None:
        """同步記錄已發送新聞"""
        try:
            if 'sent_news_log' not in self.worksheets:
                logger.error("❌ 無法找到已發送記錄工作表")
                return
            
            worksheet = self.worksheets['sent_news_log']
            
            # 準備要新增的資料
            rows_to_append = []
            for item in news_items:
                timestamp = datetime.now(self.timezone).strftime("%Y-%m-%d %H:%M:%S")
                row = [
                    timestamp,
                    item.get('title', ''),
                    item.get('summary', ''),
                    item.get('link', '')
                ]
                rows_to_append.append(row)
            
            # 批次新增資料
            if rows_to_append:
                worksheet.append_rows(rows_to_append, value_input_option='USER_ENTERED')
                logger.info(f"📝 已新增 {len(rows_to_append)} 筆記錄")
            
        except Exception as e:
            logger.error(f"記錄新聞到工作表失敗: {e}")
            raise
    
    async def add_glossary_term(self, english_term: str, taiwan_term: str) -> bool:
        """新增術語到術語表"""
        logger.info(f"➕ 新增術語: {english_term} -> {taiwan_term}")
        
        success = await asyncio.get_event_loop().run_in_executor(
            self.executor, self._sync_add_glossary_term, english_term, taiwan_term
        )
        
        if success:
            # 清除快取，強制重新載入
            self._glossary_cache = None
            self._cache_time = None
            logger.info("✅ 術語新增成功，已清除快取")
        
        return success
    
    def _sync_add_glossary_term(self, english_term: str, taiwan_term: str) -> bool:
        """同步新增術語"""
        try:
            if 'glossary' not in self.worksheets:
                logger.error("❌ 術語表工作表不存在")
                return False
            
            worksheet = self.worksheets['glossary']
            worksheet.append_row([english_term, taiwan_term])
            return True
            
        except Exception as e:
            logger.error(f"新增術語失敗: {e}")
            return False
    
    def __del__(self):
        """清理資源"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=False)
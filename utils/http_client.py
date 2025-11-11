#http_client.py (v3,3)
# -*- coding: utf-8 -*-
"""
HTTP 客戶端工具
提供統一的 HTTP 客戶端介面，支援同步和異步操作，具備重試和錯誤處理機制
"""


import asyncio
import logging
import json
import os
import requests
from typing import Dict, Any, Optional, Union
import aiohttp
from aiohttp.client_exceptions import ClientResponseError
from requests.exceptions import RequestException
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# 修正：由於我們不再於此檔案中使用 log_async_execution_time，因此移除此導入
# from utils.logger import log_async_execution_time
from utils.logger import get_logger

# 改用 get_logger 來獲取 logger 實例
logger = get_logger(__name__)

# ============================================================================
# 同步 HTTP 客戶端 (這部分保持不變)
# ============================================================================
class HTTPClient:
    """同步 HTTP 客戶端"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get('http', {})
        self.timeout = self.config.get('timeout', 15)
        self.max_retries = self.config.get('max_retries', 3)
        self.retry_delay = self.config.get('retry_delay', 1.0)
        self.user_agent = self.config.get('user_agent', 'Mozilla/5.0 (compatible; NewsBot/2.0)')
        self.default_headers = {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        self.session = requests.Session()
        self.session.headers.update(self.default_headers)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RequestException,))
    )
    def get(self, url: str, headers: Optional[Dict] = None, **kwargs) -> requests.Response:
        try:
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            response = self.session.get(
                url,
                headers=merged_headers,
                timeout=self.timeout,
                **kwargs
            )
            response.raise_for_status()
            return response
        except RequestException as e:
            logger.warning(f"⚠️ HTTP GET 失敗: {url} - {e}")
            raise
    
    # ... (post, close, __enter__, __exit__, __del__ 方法保持不變)
    def post(self, url: str, data: Optional[Union[Dict, str]] = None, 
             json: Optional[Dict] = None, headers: Optional[Dict] = None, **kwargs) -> requests.Response:
        try:
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            response = self.session.post(
                url, data=data, json=json, headers=merged_headers,
                timeout=self.timeout, **kwargs
            )
            response.raise_for_status()
            return response
        except RequestException as e:
            logger.warning(f"⚠️ HTTP POST 失敗: {url} - {e}")
            raise
    
    def close(self):
        if self.session:
            self.session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

# ============================================================================
# 異步 HTTP 客戶端
# ============================================================================
class AsyncHTTPClient:
    """異步 HTTP 客戶端"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get('http', {})
        self.ai_config = config.get('ai_api', {})
        
        self.timeout = self.config.get('timeout', 30) # 建議 AI 呼叫可以延長 timeout
        self.user_agent = self.config.get('user_agent', 'Mozilla/5.0 (compatible; NewsBot/2.0)')
        self.max_concurrent = self.config.get('max_concurrent', 10)
        
        self.default_headers = {
            'User-Agent': self.user_agent,
            'Accept': '*/*',
        }
        
        self._connector: Optional[aiohttp.TCPConnector] = None
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """延遲初始化 session，確保在異步環境中執行"""
        if self._session is None or self. _session.closed:
            self._connector = aiohttp.TCPConnector(limit=self.max_concurrent)
            self._session = aiohttp.ClientSession(
                connector=self._connector,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers=self.default_headers
            )
        return self._session

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def get_text(self, url: str, headers: Optional[Dict] = None, **kwargs) -> str:
        """異步獲取 URL 的文本內容"""
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers, **kwargs) as response:
                response.raise_for_status()
                return await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"⚠️ Async GET text 失敗: {url} - {e}")
            raise

        # @log_async_execution_time
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def post_json(self, url: str, json_payload: Dict, headers: Optional[Dict] = None, **kwargs) -> Dict:
        """
        (Gemini 新增) 異步發送 POST JSON 請求並獲取 JSON 回應
        """
        try:
            session = await self._get_session()
            
            # 準備 headers
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            # 確保 Content-Type (如果未提供)
            if 'Content-Type' not in merged_headers:
                merged_headers['Content-Type'] = 'application/json'
            
            async with session.post(url, json=json_payload, headers=merged_headers, **kwargs) as response:
                response.raise_for_status()
                # 嘗試解析 JSON 回應，如果 API 沒有回傳 body 則回傳空
                if response.status != 204: # 204 No Content
                    return await response.json()
                return {"status": "success", "code": 204}
                
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"⚠️ Async POST JSON 失敗: {url} - {e}")
            raise

    async def call_ai_api(self, prompt: str, **kwargs) -> str:
        """異步呼叫 AI API，接受動態參數"""
        session = await self._get_session()
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("環境變數 OPENROUTER_API_KEY 未設定")

        api_base = self.ai_config.get("api_base", "https://openrouter.ai/api/v1")
        
        headers = {
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "HTTP-Referer": self.ai_config.get("referer", "http://localhost"),
            "X-Title": self.ai_config.get("x_title", "LINE News Bot")
        }
        
        ai_params = ["model", "temperature", "max_tokens", "top_p", "stream"]
        payload = {"messages": [{"role": "user", "content": prompt}]}
        for param in ai_params:
            if param in kwargs:
                payload[param] = kwargs[param]

        # 檢查 model 是否真的被加入了
        if "model" not in payload:
            logger.error(f"請求 Payload 中缺少必要的 'model' 參數！請檢查 config.yaml 的拼寫（應為全小寫）。傳入的參數: {kwargs.keys()}")
            raise ValueError("請求 Payload 中缺少 'model' 參數")

        logger.info(f"準備發送 AI API 請求至模型: {payload.get('model')}")
        logger.debug(f"完整 Payload:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
        
        try:
            async with session.post(
                f"{api_base}/chat/completions", headers=headers, json=payload, 
                timeout=aiohttp.ClientTimeout(total=self.ai_config.get('timeout', 180))
            ) as response:
                response_content = await response.json()
                response.raise_for_status()
                
                response_text = response_content.get("choices", [{}])[0].get("message", {}).get("content")
                if not response_text:
                    logger.error(f"❌ AI API 回應內容為空: {response_content}")
                    raise ValueError("AI API 回應內容為空")
                
                return response_text.strip()
                
        except ClientResponseError as e:
            logger.error(f"❌ AI API HTTP 錯誤: {e.status} {e.message}")
            # 修正：aiohttp 的 ClientResponseError 沒有 .text()，但我們可以從 response_content 獲取內容
            logger.error(f"❌ API Server Response Body:\n{response_content}")
            raise
        except Exception as e:
            logger.error(f"❌ 呼叫 AI API 時發生未知錯誤: {e}", exc_info=True)
            raise

    
    async def close(self):
        """優雅地關閉 session 和 connector"""
        if self._session and not self._session.closed:
            await self._session.close()
        # aiohttp 建議不要手動關閉 connector，讓 session 管理即可
        self._session = None
        self._connector = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
# -*- coding: utf-8 -*-
"""
HTTP 客戶端工具
提供統一的 HTTP 客戶端介面，支援同步和異步操作，具備重試和錯誤處理機制
"""

import asyncio
import logging
import json
from typing import Dict, Any, Optional, Union
import aiohttp
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

class HTTPClient:
    """同步 HTTP 客戶端"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get('http', {})
        
        # HTTP 設定
        self.timeout = self.config.get('timeout', 15)
        self.max_retries = self.config.get('max_retries', 3)
        self.retry_delay = self.config.get('retry_delay', 1.0)
        self.user_agent = self.config.get('user_agent', 'Mozilla/5.0 (compatible; NewsBot/2.0)')
        
        # 預設標頭
        self.default_headers = {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        # 創建 session
        self.session = requests.Session()
        self.session.headers.update(self.default_headers)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.RequestException,))
    )
    def get(self, url: str, headers: Optional[Dict] = None, **kwargs) -> requests.Response:
        """GET 請求"""
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
            
        except requests.RequestException as e:
            logger.warning(f"⚠️ HTTP GET 失敗: {url} - {e}")
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.RequestException,))
    )
    def post(self, url: str, data: Optional[Union[Dict, str]] = None, 
             json: Optional[Dict] = None, headers: Optional[Dict] = None, **kwargs) -> requests.Response:
        """POST 請求"""
        try:
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            
            response = self.session.post(
                url,
                data=data,
                json=json,
                headers=merged_headers,
                timeout=self.timeout,
                **kwargs
            )
            response.raise_for_status()
            return response
            
        except requests.RequestException as e:
            logger.warning(f"⚠️ HTTP POST 失敗: {url} - {e}")
            raise
    
    def close(self):
        """關閉 session"""
        if self.session:
            self.session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def __del__(self):
        self.close()

class AsyncHTTPClient:
    """異步 HTTP 客戶端"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get('http', {})
        self.ai_config = config.get('ai_api', {})
        
        # HTTP 設定
        self.timeout = self.config.get('timeout', 15)
        self.user_agent = self.config.get('user_agent', 'Mozilla/5.0 (compatible; NewsBot/2.0)')
        self.max_concurrent = self.config.get('max_concurrent', 10)
        
        # 預設標頭
        self.default_headers = {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        # 連線器設定
        self.connector = aiohttp.TCPConnector(
            limit=self.max_concurrent,
            limit_per_host=5,
            ttl_dns_cache=300,
            use_dns_cache=True,
        )
        
        # Timeout 設定
        self.client_timeout = aiohttp.ClientTimeout(total=self.timeout)
        
        # Session (會在使用時創建)
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """獲取或創建 session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=self.connector,
                timeout=self.client_timeout,
                headers=self.default_headers
            )
        return self._session

    # ... 其他方法 (get, post, get_text, get_json, download_file, batch_get) 保持不變 ...
    # 由於篇幅限制，只顯示 call_ai_api 的修改部分。

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def call_ai_api(self, prompt: str, **kwargs) -> str:
        """
        異步呼叫 AI API，接受動態參數
        """
        try:
            api_key = os.getenv("OPENROUTER_API_KEY")
            api_base = self.ai_config.get("api_base", "https://openrouter.ai/api/v1")
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "messages": [{"role": "user", "content": prompt}],
                **kwargs
            }

            session = await self._get_session()
            async with session.post(f"{api_base}/chat/completions", headers=headers, json=payload, timeout=self.client_timeout) as response:
                response.raise_for_status()
                response_json = await response.json()
                
                response_text = response_json.get("choices", [{}])[0].get("message", {}).get("content")
                if not response_text:
                    logger.error(f"❌ AI API 回應內容為空: {response_json}")
                    raise ValueError("AI API 回應內容為空")
                
                return response_text
                
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                logger.error(f"AI API 呼叫失敗: Error code: 404 - 模型名稱可能無效或已下架。")
                response_content = await e.response.json() if hasattr(e.response, 'json') else e.message
                logger.error(f"詳細錯誤訊息: {response_content}")
            else:
                logger.error(f"AI API 呼叫失敗: {e.status} - {e.message}")
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"⚠️ Async HTTP POST 失敗 (AI API): {e}")
            raise
        except Exception as e:
            logger.error(f"❌ 呼叫 AI API 失敗: {e}")
            raise

    # ... 其他方法 (close, __aenter__, __aexit__, __del__, 便利函數) 保持不變 ...

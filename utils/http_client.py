# -*- coding: utf-8 -*-
"""
HTTP 客戶端工具
提供統一的 HTTP 客戶端介面，支援同步和異步操作，具備重試和錯誤處理機制
"""

import asyncio
import logging
import json
import os
import requests # <-- ADDED THIS LINE
from typing import Dict, Any, Optional, Union
import aiohttp
from aiohttp.client_exceptions import ClientResponseError
from requests.exceptions import RequestException
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.logger import log_async_execution_time

logger = logging.getLogger(__name__)

# 同步 HTTP 客戶端
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
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RequestException,))
    )
    def post(self, url: str, data: Optional[Union[Dict, str]] = None, 
             json: Optional[Dict] = None, headers: Optional[Dict] = None, **kwargs) -> requests.Response:
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
    
    def __del__(self):
        self.close()

# 異步 HTTP 客戶端
class AsyncHTTPClient:
    """異步 HTTP 客戶端"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get('http', {})
        self.ai_config = config.get('ai_api', {})
        
        self.timeout = self.config.get('timeout', 15)
        self.user_agent = self.config.get('user_agent', 'Mozilla/5.0 (compatible; NewsBot/2.0)')
        self.max_concurrent = self.config.get('max_concurrent', 10)
        
        self.default_headers = {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        self._connector: Optional[aiohttp.TCPConnector] = None
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """獲取或創建 session"""
        if self._session is None or self._session.closed:
            if self._connector is None or self._connector.closed:
                self._connector = aiohttp.TCPConnector(
                    limit=self.max_concurrent,
                    limit_per_host=5,
                    ttl_dns_cache=300,
                    use_dns_cache=True,
                )
                
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
    async def get(self, url: str, headers: Optional[Dict] = None, **kwargs) -> aiohttp.ClientResponse:
        try:
            session = await self._get_session()
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            async with session.get(url, headers=merged_headers, **kwargs) as response:
                response.raise_for_status()
                await response.read()
                return response
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"⚠️ Async HTTP GET 失敗: {url} - {e}")
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def post(self, url: str, data: Optional[Union[Dict, str]] = None,
                   json: Optional[Dict] = None, headers: Optional[Dict] = None, **kwargs) -> aiohttp.ClientResponse:
        try:
            session = await self._get_session()
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            async with session.post(url, data=data, json=json, headers=merged_headers, **kwargs) as response:
                response.raise_for_status()
                await response.read()
                return response
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"⚠️ Async HTTP POST 失敗: {url} - {e}")
            raise

    async def get_text(self, url: str, headers: Optional[Dict] = None, **kwargs) -> str:
        try:
            session = await self._get_session()
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            async with session.get(url, headers=merged_headers, **kwargs) as response:
                response.raise_for_status()
                return await response.text()
        except Exception as e:
            logger.error(f"❌ 獲取文本內容失敗: {url} - {e}")
            raise

    async def get_json(self, url: str, headers: Optional[Dict] = None, **kwargs) -> Dict:
        try:
            session = await self._get_session()
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            merged_headers['Accept'] = 'application/json, text/plain, */*'
            async with session.get(url, headers=merged_headers, **kwargs) as response:
                response.raise_for_status()
                return await response.json()
        except Exception as e:
            logger.error(f"❌ 獲取 JSON 內容失敗: {url} - {e}")
            raise

    async def download_file(self, url: str, file_path: str, headers: Optional[Dict] = None, **kwargs) -> bool:
        try:
            session = await self._get_session()
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            async with session.get(url, headers=merged_headers, **kwargs) as response:
                response.raise_for_status()
                with open(file_path, 'wb') as file:
                    async for chunk in response.content.iter_chunked(8192):
                        file.write(chunk)
                logger.info(f"✅ 檔案下載完成: {file_path}")
                return True
        except Exception as e:
            logger.error(f"❌ 檔案下載失敗: {url} - {e}")
            return False

    async def batch_get(self, urls: list, headers: Optional[Dict] = None, **kwargs) -> list:
        tasks = []
        for url in urls:
            task = self.get_text(url, headers=headers, **kwargs)
            tasks.append(task)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"⚠️ 批次請求失敗 {urls[i]}: {result}")
                processed_results.append(None)
            else:
                processed_results.append(result)
        return processed_results

    @log_async_execution_time
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
            api_base = kwargs.get("api_base", "https://openrouter.ai/api/v1")
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "messages": [{"role": "user", "content": prompt}],
                **kwargs
            }

            session = await self._get_session()
            async with session.post(f"{api_base}/chat/completions", headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=kwargs.get('timeout', self.timeout))) as response:
                response.raise_for_status()
                response_json = await response.json()
                
                response_text = response_json.get("choices", [{}])[0].get("message", {}).get("content")
                if not response_text:
                    logger.error(f"❌ AI API 回應內容為空: {response_json}")
                    raise ValueError("AI API 回應內容為空")
                
                return response_text
                
        except ClientResponseError as e:
            if e.status == 404:
                logger.error(f"AI API 呼叫失敗: Error code: 404 - 模型名稱可能無效或已下架。")
                response_content = await e.response.text() if hasattr(e.response, 'text') else e.message
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
    
    async def close(self):
        """關閉 session 和 connector"""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._connector and not self._connector.closed:
            await self._connector.close()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    def __del__(self):
        if hasattr(self, '_session') and self._session and not self._session.closed:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.close())
                else:
                    loop.run_until_complete(self.close())
            except RuntimeError:
                pass

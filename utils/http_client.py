# -*- coding: utf-8 -*-
"""
HTTP 客戶端工具
提供統一的 HTTP 客戶端介面，支援同步和異步操作，具備重試和錯誤處理機制
"""

import asyncio
import logging
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
        
        # HTTP 設定
        self.timeout = self.config.get('timeout', 15)
        self.max_retries = self.config.get('max_retries', 3)
        self.retry_delay = self.config.get('retry_delay', 1.0)
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
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def get(self, url: str, headers: Optional[Dict] = None, **kwargs) -> aiohttp.ClientResponse:
        """異步 GET 請求"""
        try:
            session = await self._get_session()
            
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            
            async with session.get(url, headers=merged_headers, **kwargs) as response:
                response.raise_for_status()
                # 讀取內容以確保完整下載
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
        """異步 POST 請求"""
        try:
            session = await self._get_session()
            
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            
            async with session.post(
                url, 
                data=data, 
                json=json, 
                headers=merged_headers, 
                **kwargs
            ) as response:
                response.raise_for_status()
                # 讀取內容以確保完整下載
                await response.read()
                return response
                
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"⚠️ Async HTTP POST 失敗: {url} - {e}")
            raise
    
    async def get_text(self, url: str, headers: Optional[Dict] = None, **kwargs) -> str:
        """獲取文本內容"""
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
        """獲取 JSON 內容"""
        try:
            session = await self._get_session()
            
            merged_headers = self.default_headers.copy()
            if headers:
                merged_headers.update(headers)
            
            # 確保 Accept header 包含 JSON
            merged_headers['Accept'] = 'application/json, text/plain, */*'
            
            async with session.get(url, headers=merged_headers, **kwargs) as response:
                response.raise_for_status()
                return await response.json()
                
        except Exception as e:
            logger.error(f"❌ 獲取 JSON 內容失敗: {url} - {e}")
            raise
    
    async def download_file(self, url: str, file_path: str, headers: Optional[Dict] = None, **kwargs) -> bool:
        """下載檔案"""
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
        """批次 GET 請求"""
        tasks = []
        for url in urls:
            task = self.get_text(url, headers=headers, **kwargs)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 處理結果
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"⚠️ 批次請求失敗 {urls[i]}: {result}")
                processed_results.append(None)
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def close(self):
        """關閉 session 和 connector"""
        if self._session and not self._session.closed:
            await self._session.close()
        if self.connector and not self.connector.closed:
            await self.connector.close()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    def __del__(self):
        # 注意：在 __del__ 中不能直接呼叫 async 函數
        if hasattr(self, '_session') and self._session and not self._session.closed:
            # 嘗試在當前事件循環中清理
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.close())
                else:
                    loop.run_until_complete(self.close())
            except RuntimeError:
                # 如果沒有事件循環或其他錯誤，忽略清理
                pass

# 便利函數
async def async_get_text(url: str, config: Dict[str, Any], headers: Optional[Dict] = None) -> Optional[str]:
    """便利函數：異步獲取文本"""
    async with AsyncHTTPClient(config) as client:
        try:
            return await client.get_text(url, headers=headers)
        except Exception as e:
            logger.error(f"❌ 獲取文本失敗: {url} - {e}")
            return None

async def async_get_json(url: str, config: Dict[str, Any], headers: Optional[Dict] = None) -> Optional[Dict]:
    """便利函數：異步獲取 JSON"""
    async with AsyncHTTPClient(config) as client:
        try:
            return await client.get_json(url, headers=headers)
        except Exception as e:
            logger.error(f"❌ 獲取 JSON 失敗: {url} - {e}")
            return None

def sync_get_text(url: str, config: Dict[str, Any], headers: Optional[Dict] = None) -> Optional[str]:
    """便利函數：同步獲取文本"""
    with HTTPClient(config) as client:
        try:
            response = client.get(url, headers=headers)
            return response.text
        except Exception as e:
            logger.error(f"❌ 獲取文本失敗: {url} - {e}")
            return None
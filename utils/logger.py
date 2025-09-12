# -*- coding: utf-8 -*-
"""
統一日誌設定模組
提供結構化日誌配置與不同環境的日誌級別
"""

import logging
import os
import sys
from typing import Optional

def setup_logger(
    name: Optional[str] = None,
    level: Optional[str] = None,
    format_style: str = 'structured'
) -> logging.Logger:
    """
    設定並返回 logger 實例
    
    Args:
        name: logger 名稱，預設為 __name__
        level: 日誌級別，預設從環境變數或設定檔讀取
        format_style: 格式風格 ('structured' 或 'simple')
    
    Returns:
        配置好的 logger 實例
    """
    
    # 確定 logger 名稱
    if name is None:
        name = __name__
    
    # 取得或建立 logger
    logger = logging.getLogger(name)
    
    # 避免重複配置
    if logger.handlers:
        return logger
    
    # 確定日誌級別
    if level is None:
        level = _determine_log_level()
    
    logger.setLevel(getattr(logging, level.upper()))
    
    # 建立處理器
    handler = _create_handler()
    
    # 設定格式器
    formatter = _create_formatter(format_style)
    handler.setFormatter(formatter)
    
    # 加入處理器到 logger
    logger.addHandler(handler)
    
    # 防止向上傳播（避免重複輸出）
    logger.propagate = False
    
    return logger

def _determine_log_level() -> str:
    """確定日誌級別"""
    # 1. 檢查環境變數
    env_level = os.getenv('LOG_LEVEL')
    if env_level:
        return env_level
    
    # 2. 根據執行環境決定
    if os.getenv('IS_CLOUD_RUN') == 'true':
        return 'INFO'  # Cloud Run 環境
    elif os.getenv('FLASK_ENV') == 'development':
        return 'DEBUG'  # 開發環境
    else:
        return 'INFO'  # 預設

def _create_handler() -> logging.Handler:
    """建立日誌處理器"""
    # Cloud Run 環境使用 stdout，本地環境也用 stdout 方便查看
    handler = logging.StreamHandler(sys.stdout)
    return handler

def _create_formatter(format_style: str) -> logging.Formatter:
    """建立日誌格式器"""
    
    if format_style == 'structured':
        # 結構化格式，適合 Cloud Run 和生產環境
        format_str = (
            '%(asctime)s | %(name)s | %(levelname)s | '
            '%(funcName)s:%(lineno)d | %(message)s'
        )
        date_format = '%Y-%m-%d %H:%M:%S'
        
    else:  # simple
        # 簡單格式，適合開發環境
        format_str = '%(asctime)s - %(levelname)s - %(message)s'
        date_format = '%H:%M:%S'
    
    return logging.Formatter(format_str, datefmt=date_format)

def configure_third_party_loggers(level: str = 'WARNING'):
    """配置第三方套件的日誌級別，減少噪音"""
    third_party_loggers = [
        'urllib3.connectionpool',
        'requests.packages.urllib3',
        'googleapiclient.discovery',
        'google.auth.transport.requests',
        'openai',
        'httpx',
        'aiohttp.access'
    ]
    
    for logger_name in third_party_loggers:
        logging.getLogger(logger_name).setLevel(getattr(logging, level.upper()))

# 全域日誌配置函數
def setup_global_logging(level: str = 'INFO'):
    """設定全域日誌配置"""
    # 設定根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # 清除現有處理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 加入新處理器
    handler = _create_handler()
    formatter = _create_formatter('structured')
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    
    # 配置第三方日誌
    configure_third_party_loggers()
    
    logging.info(f"全域日誌配置完成，級別: {level}")

# 便利函數：為特定模組建立 logger
def get_module_logger(module_name: str) -> logging.Logger:
    """為特定模組建立 logger"""
    return setup_logger(module_name)

# 性能監控日誌裝飾器
def log_execution_time(logger: logging.Logger = None):
    """日誌裝飾器：記錄函數執行時間"""
    import time
    from functools import wraps
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            
            # 使用提供的 logger 或建立新的
            log = logger or setup_logger(func.__module__)
            
            try:
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time
                log.info(f"✅ {func.__name__} 執行完成，耗時: {execution_time:.2f}s")
                return result
                
            except Exception as e:
                execution_time = time.time() - start_time
                log.error(f"❌ {func.__name__} 執行失敗，耗時: {execution_time:.2f}s，錯誤: {e}")
                raise
                
        return wrapper
    return decorator

# 異步版本的執行時間記錄裝飾器
def log_async_execution_time(logger: logging.Logger = None):
    """異步日誌裝飾器：記錄異步函數執行時間"""
    import time
    import asyncio
    from functools import wraps
    
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            
            # 使用提供的 logger 或建立新的
            log = logger or setup_logger(func.__module__)
            
            try:
                result = await func(*args, **kwargs)
                execution_time = time.time() - start_time
                log.info(f"✅ {func.__name__} 異步執行完成，耗時: {execution_time:.2f}s")
                return result
                
            except Exception as e:
                execution_time = time.time() - start_time
                log.error(f"❌ {func.__name__} 異步執行失敗，耗時: {execution_time:.2f}s，錯誤: {e}")
                raise
                
        return wrapper
    return decorator
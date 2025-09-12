# core/__init__.py
# -*- coding: utf-8 -*-
"""
LINE Bot 新聞推播系統核心模組
"""

__version__ = "2.0.0"
__author__ = "LINE Bot News System"

# 匯出核心模組的主要類別供外部使用
from .sheet_manager import SheetManager # <--- 確認這行存在
from .news_fetcher import NewsFetcher
from .news_classifier import OptimizedNewsClassifier
from .news_processor import NewsProcessor
from .line_notifier import LineNotifier

__all__ = [
    'SheetManager',
    'NewsFetcher',
    'OptimizedNewsClassifier',
    'NewsProcessor',
    'LineNotifier',
]

# ============================================================================

# utils/__init__.py  
# -*- coding: utf-8 -*-
"""
工具模組
"""

from utils.logger import setup_logger, get_module_logger, log_execution_time, log_async_execution_time
from utils.http_client import AsyncHTTPClient, HTTPClient

__all__ = [
    'setup_logger',
    'get_module_logger', 
    'log_execution_time',
    'log_async_execution_time',
    'AsyncHTTPClient',
    'HTTPClient',
]

# ============================================================================

# tests/__init__.py
# -*- coding: utf-8 -*-
"""
測試模組
"""

# 測試工具和設定可以在這裡匯入
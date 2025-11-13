# core/__init__.py
# -*- coding: utf-8 -*-
"""
核心功能模組

This file defines the public interface of the 'core' package,
making it easy to import core classes from other parts of the application.
"""

# Import the main classes from the modules within the 'core' directory
from .line_notifier import LineNotifier
from .news_classifier import OptimizedNewsClassifier as NewsClassifier
from .news_fetcher import NewsFetcher
from .news_processor import NewsProcessor
from .sheet_manager import SheetManager

# Define what gets imported when someone uses "from core import *"
__all__ = [
    "LineNotifier",
    "NewsClassifier",
    "NewsFetcher",
    "NewsProcessor",
    "SheetManager",
]

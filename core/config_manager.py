# -*- coding: utf-8 -*-
"""
設定檔管理器
負責載入 config.yaml 並提供設定值存取介面
"""

import os
import yaml
from typing import Dict, Any, Optional
from pathlib import Path

class ConfigManager:
    """設定檔管理器 - 單例模式"""
    
    _instance = None
    _config = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._config is None:
            self._load_config()
    
    def _load_config(self) -> None:
        """載入設定檔"""
        config_path = self._find_config_file()
        
        with open(config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)
        
        # 應用環境變數覆蓋
        self._apply_env_overrides()
    
    def _find_config_file(self) -> Path:
        """尋找設定檔的位置"""
        possible_paths = [
            Path("config.yaml"),
            Path("../config.yaml"),  # 從 core/ 目錄執行時
            Path(__file__).parent.parent / "config.yaml"
        ]
        
        for path in possible_paths:
            if path.exists():
                return path
        
        raise FileNotFoundError(
            f"找不到 config.yaml，已搜尋位置: {[str(p) for p in possible_paths]}"
        )
    
    def _apply_env_overrides(self) -> None:
        """應用環境變數覆蓋設定"""
        env_overrides = {
            # AI 模型覆蓋
            'AI_CLASSIFICATION_MODEL': ['ai_models', 'classification', 'name'],
            'AI_SUMMARIZATION_MODEL': ['ai_models', 'summarization', 'name'],
            
            # 處理參數覆蓋
            'MAX_CONCURRENT_REQUESTS': ['classifier', 'max_concurrent'],
            'BATCH_SIZE': ['classifier', 'batch_size'],
            'MAX_FINAL_NEWS': ['news_processing', 'max_final_news'],
            
            # Timeout 覆蓋
            'MAX_EXECUTION_TIME': ['cloud_run', 'max_execution_time'],
        }
        
        for env_var, config_path in env_overrides.items():
            env_value = os.getenv(env_var)
            if env_value is not None:
                # 嘗試轉換型別
                try:
                    if env_value.isdigit():
                        env_value = int(env_value)
                    elif env_value.replace('.', '').isdigit():
                        env_value = float(env_value)
                except:
                    pass  # 保持字串型別
                
                # 設定到對應的路徑
                self._set_nested_value(self._config, config_path, env_value)
    
    def _set_nested_value(self, config: Dict, path: list, value: Any) -> None:
        """設定巢狀字典的值"""
        current = config
        for key in path[:-1]:
            current = current.setdefault(key, {})
        current[path[-1]] = value
    
    def get_config(self) -> Dict[str, Any]:
        """獲取完整設定"""
        return self._config.copy()
    
    def get(self, *path: str, default: Any = None) -> Any:
        """獲取特定設定值"""
        current = self._config
        try:
            for key in path:
                current = current[key]
            return current
        except (KeyError, TypeError):
            return default
    
    def get_rss_feeds(self) -> Dict[str, str]:
        """獲取 RSS 新聞來源"""
        return self.get('news_sources', 'rss_feeds', default={})
    
    def get_html_sources(self) -> Dict[str, Dict]:
        """獲取 HTML 新聞來源"""
        return self.get('news_sources', 'html_sources', default={})
    
    def get_ai_model_config(self, model_type: str) -> Dict[str, Any]:
        """獲取 AI 模型設定"""
        return self.get('ai_models', model_type, default={})
    
    def get_classifier_config(self) -> Dict[str, Any]:
        """獲取分類器設定"""
        return self.get('classifier', default={})
    
    def get_http_config(self) -> Dict[str, Any]:
        """獲取 HTTP 設定"""
        return self.get('http', default={})
    
    def get_sheets_config(self) -> Dict[str, Any]:
        """獲取 Google Sheets 設定"""
        return self.get('google_sheets', default={})
    
    def get_line_config(self) -> Dict[str, Any]:
        """獲取 LINE Bot 設定"""
        return self.get('line_bot', default={})
    
    def get_processing_config(self) -> Dict[str, Any]:
        """獲取新聞處理設定"""
        return self.get('news_processing', default={})
    
    def get_cloud_run_config(self) -> Dict[str, Any]:
        """獲取 Cloud Run 設定"""
        return self.get('cloud_run', default={})
    
    def is_development(self) -> bool:
        """檢查是否為開發環境"""
        return os.getenv("IS_CLOUD_RUN") != "true"
    
    def get_timezone(self) -> str:
        """獲取時區設定"""
        return self.get('app', 'timezone', default='Asia/Taipei')
    
    def get_default_translations(self) -> Dict[str, str]:
        """獲取預設翻譯對照表"""
        return self.get('default_translations', default={})
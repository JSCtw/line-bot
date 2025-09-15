# utils/config_manager.py (0915優化版本)
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
    """設定檔管理器 - 使用單例模式確保全局唯一實例"""
    
    _instance = None
    _config: Optional[Dict[str, Any]] = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load_config(self, config_path: Optional[str] = None) -> Dict[str, Any]:
        """
        載入設定檔。如果已載入，則直接返回快取的設定。
        可選參數 config_path 用於指定設定檔路徑，主要用於測試。
        """
        if self._config is not None:
            return self.config

        if config_path:
            cfg_path = Path(config_path)
        else:
            cfg_path = self._find_config_file()
            
        if not cfg_path.exists():
            raise FileNotFoundError(f"設定檔不存在: {cfg_path}")

        print(f"正在從 {cfg_path.resolve()} 載入設定檔...") # 增加一個 print 方便除錯

        with open(cfg_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)
        
        self._apply_env_overrides()
        
        return self.config

    @property
    def config(self) -> Dict[str, Any]:
        """以屬性 (property) 的方式安全地獲取設定字典"""
        if self._config is None:
            # 如果尚未載入，則自動載入一次
            self.load_config()
        # 回傳一個淺拷貝，防止外部程式碼意外修改內部設定
        return self._config.copy()

    def _find_config_file(self) -> Path:
        """從專案根目錄向上尋找 config.yaml"""
        # 從目前工作目錄開始尋找
        current_dir = Path.cwd()
        for _ in range(5): # 最多向上尋找 5 層
            config_file = current_dir / "config.yaml"
            if config_file.exists():
                return config_file
            if current_dir.parent == current_dir: # 到達根目錄
                break
            current_dir = current_dir.parent
            
        raise FileNotFoundError("在專案目錄中找不到 config.yaml")
    
    def _apply_env_overrides(self) -> None:
        """應用環境變數覆蓋設定"""
        # (這部分邏輯與您原本的完全相同，無需修改)
        if self._config is None:
            return

        env_overrides = {
            'AI_CLASSIFICATION_MODEL': ['ai_models', 'classification', 'model'],
            'AI_SUMMARIZATION_MODEL': ['ai_models', 'summarization', 'model'],
            'MAX_CONCURRENT_REQUESTS': ['classifier', 'max_concurrent'],
            # ... 其他您定義的環境變數 ...
        }
        
        for env_var, config_path in env_overrides.items():
            env_value = os.getenv(env_var)
            if env_value is not None:
                try:
                    if env_value.isdigit():
                        env_value = int(env_value)
                    elif env_value.replace('.', '', 1).isdigit():
                        env_value = float(env_value)
                except ValueError:
                    pass
                
                self._set_nested_value(self._config, config_path, env_value)
    
    def _set_nested_value(self, config_dict: Dict, path: list, value: Any):
        """遞迴設定巢狀字典的值"""
        key = path[0]
        if len(path) == 1:
            config_dict[key] = value
        else:
            config_dict.setdefault(key, {})
            self._set_nested_value(config_dict[key], path[1:], value)
            
    def get(self, *path: str, default: Any = None) -> Any:
        """透過路徑獲取特定設定值"""
        current = self.config
        try:
            for key in path:
                current = current[key]
            return current
        except (KeyError, TypeError):
            return default

# 您其他的 get_* 方法可以保留，它們基於 get() 方法，是很好的快捷方式
# 例如:
# def get_rss_feeds(self) -> Dict[str, str]:
#     return self.get('news_sources', 'rss_feeds', default={})
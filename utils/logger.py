# utils/logger.py (v3.3)

import logging
import time
import functools

# [v3.3 修正]
# 移除了所有 logging.basicConfig()。
# 在 Cloud Run/Gunicorn 環境中，日誌設定應由 Gunicorn (入口) 
# 自動處理 (預設輸出到 stdout/stderr)。
# 在模組中手動設定會導致衝突或日誌重複。

def get_logger(name):
    """獲取一個 logger 實例"""
    return logging.getLogger(name)

# --- 裝飾器 (此部分是 100% 正確且健壯的) ---
def log_async_execution_time(task_name: str = ""):
    """
    一個功能更強大的異步函式執行時間紀錄裝飾器。
    它能自動使用被裝飾的函式名稱作為任務名，並正確處理所有參數。
    """
    def decorator(func):
        # 使用 functools.wraps 來保留原始函式的元數據
        @functools.wraps(func)
        # 修正核心：在 wrapper 中同時使用 *args 和 **kwargs
        async def wrapper(*args, **kwargs):
            # 如果沒有提供 task_name, 就使用函式自己的名字
            effective_task_name = task_name if task_name else func.__name__
            
            # 自動獲取 logger (這非常棒)
            logger = get_logger(func.__module__)
            logger.info(f"🚀 開始執行異步任務: {effective_task_name}...")
            
            start_time = time.time()
            try:
                # 將 *args 和 **kwargs 原封不動地傳遞给原始函式
                result = await func(*args, **kwargs)
                return result
            finally:
                end_time = time.time()
                elapsed_time = end_time - start_time
                logger.info(f"✅ 異步任務 '{effective_task_name}' 執行完成，耗時: {elapsed_time:.2f}s")
        return wrapper
    return decorator
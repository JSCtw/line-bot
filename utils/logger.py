# utils/logger.py 

import logging
import time
import functools

# 假設您的 logger 設定也在這個檔案中
# 如果不在，請確保 logging 被正確匯入
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

def get_logger(name):
    return logging.getLogger(name)

# --- 這是需要修正的裝飾器 ---
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
            
            logger = get_logger(func.__module__) # 自動獲取 logger
            logger.info(f"🚀 開始執行異步任務: {effective_task_name}...")
            
            start_time = time.time()
            try:
                # 修正核心：將 *args 和 **kwargs 原封不動地傳遞給原始函式
                result = await func(*args, **kwargs)
                return result
            finally:
                end_time = time.time()
                elapsed_time = end_time - start_time
                logger.info(f"✅ 異步任務 '{effective_task_name}' 執行完成，耗時: {elapsed_time:.2f}s")
        return wrapper
    return decorator
import time


def now_ms() -> int:
    """UTC 毫秒时间戳；数据库里的时间字段一律用它。"""
    return int(time.time() * 1000)

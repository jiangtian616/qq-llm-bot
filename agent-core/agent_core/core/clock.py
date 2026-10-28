import time


def now_ms() -> int:
    """UTC 毫秒时间戳；数据库里的时间字段一律用它。"""
    return int(time.time() * 1000)


def start_of_local_day_ms() -> int:
    """本地时区今天 0 点的毫秒时间戳；每日预算按本地日期算。"""
    import datetime

    now = datetime.datetime.now().astimezone()
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

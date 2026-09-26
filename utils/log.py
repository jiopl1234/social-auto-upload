import sys
from pathlib import Path
from typing import Any
from loguru import logger

from conf import BASE_DIR


stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
if callable(stdout_reconfigure):
    stdout_reconfigure(encoding="utf-8", errors="replace")
if callable(stderr_reconfigure):
    stderr_reconfigure(encoding="utf-8", errors="replace")


def log_formatter(record: Any) -> str:
    """
    Formatter for log records.
    :param dict record: Log object containing log metadata & message.
    :returns: str
    """
    colors = {
        "TRACE": "#cfe2f3",
        "INFO": "#9cbfdd",
        "DEBUG": "#8598ea",
        "WARNING": "#dcad5a",
        "SUCCESS": "#3dd08d",
        "ERROR": "#ae2c2c"
    }
    color = colors.get(record["level"].name, "#b3cfe7")
    return f"<fg #70acde>{{time:YYYY-MM-DD HH:mm:ss}}</fg #70acde> | <fg {color}>{{level}}</fg {color}>: <light-white>{{message}}</light-white>\n"


def _safe_record(record: Any) -> None:
    # Legacy uploaders can log page text, URLs, cookies, or exception objects.
    # Their arbitrary prose is not a safe diagnostic interface. The task store
    # carries structured, allowlisted evidence for investigation instead.
    record["message"] = "Application event"
    record["exception"] = None


def create_logger(log_name: str, file_path: str):
    """
    Create custom logger for different business modules.
    :param str log_name: name of log
    :param str file_path: Optional path to log file
    :returns: Configured logger
    """
    def filter_record(record):
        return record["extra"].get("business_name") == log_name

    Path(BASE_DIR / file_path).parent.mkdir(exist_ok=True)
    logger.add(Path(BASE_DIR / file_path), filter=filter_record, level="INFO", rotation="10 MB", retention="10 days", backtrace=False, diagnose=False)
    return logger.bind(business_name=log_name)


# Remove all existing handlers
logger.remove()
logger.configure(patcher=_safe_record)
# Add a standard console handler
logger.add(sys.stderr, colorize=True, format=log_formatter, backtrace=False, diagnose=False)

douyin_logger = create_logger('douyin', 'logs/douyin.log')
tencent_logger = create_logger('tencent', 'logs/tencent.log')
xhs_logger = create_logger('xhs', 'logs/xhs.log')
tiktok_logger = create_logger('tiktok', 'logs/tiktok.log')
bilibili_logger = create_logger('bilibili', 'logs/bilibili.log')
kuaishou_logger = create_logger('kuaishou', 'logs/kuaishou.log')
baijiahao_logger = create_logger('baijiahao', 'logs/baijiahao.log')
xiaohongshu_logger = create_logger('xiaohongshu', 'logs/xiaohongshu.log')
youtube_logger = create_logger('youtube', 'logs/youtube.log')
alipay_logger = create_logger('alipay', 'logs/alipay.log')
weibo_logger = create_logger('weibo', 'logs/weibo.log')
hupu_logger = create_logger('hupu', 'logs/hupu.log')

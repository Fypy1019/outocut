"""统一日志配置：所有引擎日志以中文输出到 stderr 与日志文件。

Electron 桌面端会捕获引擎的 stderr 写入 desktop.log（设置 -> 日志），
同时引擎也会把日志写入数据目录下的 logs/engine.log，双通道保证可追溯。
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_CONFIGURED = False


def setup_logging(data_root: Path) -> None:
    """配置根日志器（幂等）：stderr + 数据目录 logs/engine.log。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    formatter = logging.Formatter(_FORMAT)

    # Windows 下 stderr 默认可能是 GBK，统一改成 UTF-8，保证 Electron 按 UTF-8 解码不乱码。
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

    if sys.stderr is not None:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        root.addHandler(stderr_handler)

    try:
        log_dir = data_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "engine.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # 日志文件不可用时不能影响引擎启动。
        pass

    logging.getLogger("outocut_engine").info("引擎日志已初始化，数据目录：%s", data_root)

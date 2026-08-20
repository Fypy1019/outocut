from __future__ import annotations

import argparse

import uvicorn

from outocut_engine.config import Settings
from outocut_engine.engine_log import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="OutoCut local engine")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=35006)
    args = parser.parse_args()
    settings = Settings.from_env()
    setup_logging(settings.data_root)
    uvicorn.run(
        "outocut_engine.app:create_app",
        host=args.host,
        port=args.port,
        factory=True,
        log_level="info",
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()

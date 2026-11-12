"""main.py — エントリポイント

このプロジェクトの入口。`実行.bat` か `python main.py` で実行する。

処理の本体は `browser_report/` 以下に書き、ここでは起動とログの設定だけを行う。
"""

from comken import comken_logger

from browser_report.run import main

if __name__ == "__main__":
    comken_logger.setup_local_logging()
    main()

"""讓 ``python -m log_analyzer`` 與安裝後的 log-analyzer 指令共用入口。"""

from .presentation.cli import main


if __name__ == "__main__":
    # 直接執行模組才啟動；其他檔案 import 本模組時不會自動開啟介面。
    main()

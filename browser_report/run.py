r"""browser_report/run.py — 実行の入口。

本体（Salesforceレポートダウンローダー）を ``sys.path`` へ足して読み込み、
本体の ``download_scheduled()`` にブラウザ取得の関数を渡す。

**本体を実行したあとにこのリポジトリを実行する** のが運用の前提である。
本体が取った行は「当日成功済み」なので対象選定に残らず、ここでは
**本体の取り残し（Report API の打ち切り・0 件・SOQL の失敗・本体が飛ばした
「2000件超」の行など）が全部** ブラウザで取られる。

    設定（config.ini の [DOWNLOADER] PATH）
            ↓
    本体のフォルダを sys.path へ足す
            ↓
    from src.service import download_scheduled
            ↓
    download_scheduled("ブラウザ取得", fetch_browser=BrowserFetcher())

管理表・保存先・履歴・排他はすべて本体が持つ。このリポジトリは持たない。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from comken import config
from comken.exceptions import ComkenFileNotFoundError

from browser_report.fetch import BrowserFetcher

logger = logging.getLogger(__name__)

# 履歴の「プロジェクト」列に残る名前。誰が取ったかを後から追えるようにする
PROJECT_NAME = "ブラウザ取得"


def read_downloader_path() -> Path:
    """``config.ini`` の ``[DOWNLOADER] PATH`` から本体のフォルダを返す。"""
    return Path(config.DOWNLOADER.PATH)


def check_downloader(downloader_path: Path) -> None:
    """本体のフォルダと取得パッケージ（``src/service``）があるか確かめる。

    Raises:
        ComkenFileNotFoundError: 本体のフォルダか ``src/service`` が見つからないとき。
    """
    if not downloader_path.is_dir():
        raise ComkenFileNotFoundError(
            "本体のフォルダ",
            downloader_path,
            "config.ini の [DOWNLOADER] PATH に Salesforceレポートダウンローダーの"
            "フォルダを正しく書いてください。",
        )
    service_path = downloader_path / "src" / "service"
    if not service_path.is_dir():
        raise ComkenFileNotFoundError(
            "本体の取得パッケージ（src/service）",
            service_path,
            "PATH には Salesforceレポートダウンローダーのフォルダを指定してください。",
        )


def add_downloader_to_path(downloader_path: Path) -> None:
    """本体のフォルダを ``sys.path`` の先頭へ入れる。

    本体は ``src`` パッケージで配布されているので、フォルダごと ``sys.path`` へ
    入れないと ``from src.service import ...`` を読めない。
    """
    path_text = str(downloader_path)
    if path_text in sys.path:
        return
    sys.path.insert(0, path_text)


def main(downloader_path: Path | None = None) -> None:
    """本体の取り残しをブラウザで全部取る。

    Args:
        downloader_path: 本体のフォルダ。省略時は ``config.ini`` の
            ``[DOWNLOADER] PATH`` を読む。

    Raises:
        ComkenFileNotFoundError: 本体のフォルダか ``src/service`` が見つからないとき。
    """
    if downloader_path is None:
        downloader_path = read_downloader_path()
    check_downloader(downloader_path)
    add_downloader_to_path(downloader_path)

    # 本体の ``src`` パッケージは、上の ``add_downloader_to_path()`` で
    # ``sys.path`` に入ってから読む（読む順番が先だと見つからない）
    from src.service import download_scheduled

    logger.info("ブラウザ取得を始めます: 本体=%s", downloader_path)
    with BrowserFetcher() as fetch:
        saved = download_scheduled(PROJECT_NAME, fetch_browser=fetch)
    logger.info("%d 件を取得しました。", len(saved))

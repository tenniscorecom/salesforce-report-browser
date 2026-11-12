"""``browser_report/run.py`` のテスト。

本体（``src`` パッケージ）は偽物を使う。``sys.path`` へ入れる先用の一時フォルダに
``src/service`` だけの偽の本体を作り、そこを ``sys.path`` へ足して
``from src.service import download_scheduled`` が偽物を拾うことを確かめる。

本物のブラウザ・Salesforce には接続しない。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from comken.exceptions import ComkenFileNotFoundError

from browser_report import run as run_module
from browser_report.fetch import BrowserFetcher

# 偽の本体。download_scheduled() の呼び出し内容を記録するだけの偽物
FAKE_DOWNLOADER_PACKAGE = '''\
"""偽の本体。download_scheduled() の呼び出し内容を記録する。"""

CALLS = []


def download_scheduled(project="定期実行", *, filters_by_report=None, fetch_browser=None):
    CALLS.append(
        {
            "project": project,
            "filters_by_report": filters_by_report,
            "fetch_browser": fetch_browser,
        }
    )
    return []
'''


@pytest.fixture
def fake_downloader(tmp_path: Path) -> Iterator[Path]:
    """``src/service`` を持つ偽の本体フォルダを作る。

    ``sys.path`` と ``sys.modules`` は元の状態へ戻す（他のテストへ影響を残さない）。
    """
    downloader_path = tmp_path / "downloader"
    service_path = downloader_path / "src" / "service"
    service_path.mkdir(parents=True)
    (downloader_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (service_path / "__init__.py").write_text(FAKE_DOWNLOADER_PACKAGE, encoding="utf-8")

    saved_path = sys.path[:]
    try:
        yield downloader_path
    finally:
        sys.path[:] = saved_path
        for name in ("src.service", "src"):
            sys.modules.pop(name, None)


class TestMainCallsDownloadScheduledWithBrowserFetcher:
    """本体の ``download_scheduled()`` にブラウザ取得の関数を渡す。"""

    def test_passes_browser_fetcher_as_fetch_browser(self, fake_downloader):
        run_module.main(downloader_path=fake_downloader)

        service = sys.modules["src.service"]
        assert len(service.CALLS) == 1
        call = service.CALLS[0]
        assert call["project"] == "ブラウザ取得"
        assert isinstance(call["fetch_browser"], BrowserFetcher)

    def test_adds_downloader_folder_to_sys_path(self, fake_downloader):
        run_module.main(downloader_path=fake_downloader)

        assert sys.path[0] == str(fake_downloader)


class TestMainStopsWhenDownloaderIsMissing:
    """本体のフォルダか ``src/service`` が無いときは分かるメッセージで止まる。"""

    def test_stops_when_folder_is_missing(self, tmp_path):
        missing_path = tmp_path / "存在しないフォルダ"

        with pytest.raises(ComkenFileNotFoundError, match="本体のフォルダ"):
            run_module.main(downloader_path=missing_path)

    def test_stops_when_service_package_is_missing(self, tmp_path):
        downloader_path = tmp_path / "downloader"
        (downloader_path / "src").mkdir(parents=True)

        with pytest.raises(ComkenFileNotFoundError, match="src/service"):
            run_module.main(downloader_path=downloader_path)


class TestReadDownloaderPath:
    """本体のフォルダは ``config.ini`` の ``[DOWNLOADER] PATH`` から読む。"""

    def test_reads_path_from_config(self, monkeypatch):
        monkeypatch.setattr(
            run_module,
            "config",
            SimpleNamespace(DOWNLOADER=SimpleNamespace(PATH=r"F:\dev\本体フォルダ")),
        )

        assert run_module.read_downloader_path() == Path(r"F:\dev\本体フォルダ")

"""``browser_report/fetch.py`` のテスト。

本物のブラウザ・Salesforce には接続しない。ブラウザも本体（``src`` パッケージ）も
偽物を使う。

    同じ組織の 2 件 → ブラウザは 1 つ・ログインは 1 回
    違う組織       → 別々に開く
    取得で例外     → そのブラウザを閉じ、次の同組織の行で開き直してログインし直す
    ログインで例外 → 同上
    with を抜けた  → 開いたブラウザが全部 __exit__(None, None, None) で閉じられる
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from browser_report.fetch import BrowserFetcher

# 取得に使う CSV（中身は結果の確認にしか使わないので、適当な値にしてある）
CSV_BYTES = b"col,amt\nv,1\n"

# 組織ごとの偽物.site_for() は URL にこの文字列を含んでいるかで組織を引く
ORG_A = "example.my.salesforce.com"
ORG_B = "other.my.salesforce.com"

URL_A = f"https://{ORG_A}/lightning/r/Report/00O5g00000ABCDE/view"
URL_B = f"https://{ORG_B}/lightning/r/Report/00O5g00000FGHIJ/view"


@dataclass(frozen=True)
class FakeEntry:
    """本体の管理表の 1 行の偽物（``key`` と ``url`` だけ使う）。"""

    key: str
    url: str


class FakeBrowser:
    """ブラウザの偽物。開いた数・ログイン回数・閉じたときの引数を記録する。"""

    def __init__(self, site: FakeSiteClass) -> None:
        self.site = site
        self.enter_count = 0
        self.login_count = 0
        self.export_count = 0
        self.exit_args: list[tuple] = []

    def __enter__(self) -> FakeBrowser:
        self.enter_count += 1
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.exit_args.append((exc_type, exc, tb))
        if self.site.exit_raises:
            raise RuntimeError("閉じるときに失敗しました")

    def login_with_credentials(self) -> None:
        self.site.login_calls += 1
        self.login_count += 1
        if self.site.fail_login_at == self.site.login_calls:
            raise RuntimeError("ログインに失敗しました")

    def export_reports(self, reports: dict[str, Path]) -> Iterator[tuple[str, Path]]:
        self.site.export_calls += 1
        self.export_count += 1
        if self.site.fail_export_at == self.site.export_calls:
            raise RuntimeError("エクスポートに失敗しました")
        for _url, destination in reports.items():
            Path(destination).write_bytes(self.site.csv_bytes)
            self.site.exported_paths.append(Path(destination))
            yield "00O5g00000ABCDE", Path(destination)


class FakeSiteClass:
    """サイトクラス（組織）の偽物。呼ぶたびに新しいブラウザを返す。"""

    def __init__(self, csv_bytes: bytes = CSV_BYTES) -> None:
        self.csv_bytes = csv_bytes
        self.browsers: list[FakeBrowser] = []
        self.exported_paths: list[Path] = []
        self.login_calls = 0
        self.export_calls = 0
        # 何回目のログイン／エクスポートで例外を出すか（1 始まり。None なら出さない）
        self.fail_login_at: int | None = None
        self.fail_export_at: int | None = None
        # 閉じるときにも例外を出すか
        self.exit_raises = False

    def __call__(self) -> FakeBrowser:
        browser = FakeBrowser(self)
        self.browsers.append(browser)
        return browser


def _patch_site_for(sites: dict[str, FakeSiteClass]):
    """``site_for()`` を偽物に差し替える。URL に含まれるドメインで組織を引く。"""

    def _site_for(url: str) -> FakeSiteClass:
        for domain, site in sites.items():
            if domain in url:
                return site
        raise AssertionError(f"知らない URL です: {url}")

    return patch("comken.toolbox.browser.sites.salesforce.site_for", side_effect=_site_for)


class TestBrowserFetcherReusesOneBrowserPerOrg:
    """組織ごとにブラウザを 1 つだけ開き、ログインも 1 回だけにする。"""

    def test_same_org_opens_one_browser_and_logs_in_once(self):
        site = FakeSiteClass()
        entry_a = FakeEntry(key="9001", url=URL_A)
        entry_b = FakeEntry(key="9002", url=URL_A)

        with _patch_site_for({ORG_A: site}):
            with BrowserFetcher() as fetch:
                table_a = fetch(entry_a)
                table_b = fetch(entry_b)

        # ブラウザは 1 つだけ。ログインも 1 回だけ（2 件目は使い回す）
        assert len(site.browsers) == 1
        browser = site.browsers[0]
        assert browser.enter_count == 1
        assert browser.login_count == 1
        # エクスポートはレポートごとに毎回
        assert browser.export_count == 2
        # 返り値は CSV を読んだ Table
        assert table_a.columns == ["col", "amt"]
        assert list(table_a) == [{"col": "v", "amt": "1"}]
        assert list(table_b) == [{"col": "v", "amt": "1"}]

    def test_different_orgs_open_separate_browsers(self):
        site_a = FakeSiteClass()
        site_b = FakeSiteClass()
        entry_a = FakeEntry(key="9001", url=URL_A)
        entry_b = FakeEntry(key="9002", url=URL_B)

        with _patch_site_for({ORG_A: site_a, ORG_B: site_b}):
            with BrowserFetcher() as fetch:
                fetch(entry_a)
                fetch(entry_b)
                fetch(entry_a)

        # 組織ごとに 1 つずつ
        assert len(site_a.browsers) == 1
        assert len(site_b.browsers) == 1
        assert site_a.browsers[0].login_count == 1
        assert site_b.browsers[0].login_count == 1
        assert site_a.browsers[0].export_count == 2
        assert site_b.browsers[0].export_count == 1

    def test_temporary_csv_is_removed_after_fetch(self):
        """エクスポート先の一時ファイルは残らない。"""
        site = FakeSiteClass()
        entry = FakeEntry(key="9001", url=URL_A)

        with _patch_site_for({ORG_A: site}):
            with BrowserFetcher() as fetch:
                fetch(entry)

        assert site.exported_paths, "エクスポート先の一時ファイルが記録されていない"
        for path in site.exported_paths:
            assert not path.exists(), f"一時ファイルが残っています: {path}"


class TestBrowserFetcherDiscardsBrokenBrowser:
    """失敗したブラウザは閉じ、使い回しの入れ物から外す。"""

    def test_export_failure_closes_browser_and_reopens_for_next_entry(self):
        site = FakeSiteClass()
        site.fail_export_at = 1
        entry = FakeEntry(key="9001", url=URL_A)

        with _patch_site_for({ORG_A: site}):
            with BrowserFetcher() as fetch:
                with pytest.raises(RuntimeError, match="エクスポートに失敗しました"):
                    fetch(entry)
                # 失敗したブラウザは閉じられている
                assert site.browsers[0].exit_args == [(None, None, None)]
                # 次の同組織の行では新しいブラウザを開いてログインし直す
                table = fetch(entry)

        assert len(site.browsers) == 2
        assert site.browsers[0].login_count == 1
        assert site.browsers[1].login_count == 1
        assert table.columns == ["col", "amt"]

    def test_login_failure_closes_browser_and_reopens_for_next_entry(self):
        site = FakeSiteClass()
        site.fail_login_at = 1
        entry = FakeEntry(key="9001", url=URL_A)

        with _patch_site_for({ORG_A: site}):
            with BrowserFetcher() as fetch:
                with pytest.raises(RuntimeError, match="ログインに失敗しました"):
                    fetch(entry)
                # ログインに失敗したブラウザも閉じられている
                assert site.browsers[0].exit_args == [(None, None, None)]
                # 次の同組織の行では新しいブラウザを開いてログインし直す
                fetch(entry)

        assert len(site.browsers) == 2
        assert site.browsers[0].login_count == 1
        assert site.browsers[1].login_count == 1

    def test_close_failure_does_not_hide_original_error(self, caplog):
        """閉じる途中で例外が出ても、元の失敗はそのまま上がる（隠さない）。"""
        site = FakeSiteClass()
        site.fail_export_at = 1
        site.exit_raises = True
        entry = FakeEntry(key="9001", url=URL_A)

        with _patch_site_for({ORG_A: site}):
            with BrowserFetcher() as fetch:
                with pytest.raises(RuntimeError, match="エクスポートに失敗しました"):
                    fetch(entry)

        assert "ブラウザを閉じる途中で例外が発生しました" in caplog.text


class TestBrowserFetcherClosesBrowsers:
    """``with`` を抜けたときに開いたブラウザを全部閉じる。"""

    def test_exit_closes_every_browser_with_none(self):
        site_a = FakeSiteClass()
        site_b = FakeSiteClass()
        entry_a = FakeEntry(key="9001", url=URL_A)
        entry_b = FakeEntry(key="9002", url=URL_B)

        with _patch_site_for({ORG_A: site_a, ORG_B: site_b}):
            with BrowserFetcher() as fetch:
                fetch(entry_a)
                fetch(entry_b)
                # ``with`` を抜ける前はまだ閉じられていない
                assert site_a.browsers[0].exit_args == []
                assert site_b.browsers[0].exit_args == []

        # 例外情報を渡さない（``None`` のまま）。渡すと正常終了でも
        # エラー時のスクリーンショットが撮られるため
        assert site_a.browsers[0].exit_args == [(None, None, None)]
        assert site_b.browsers[0].exit_args == [(None, None, None)]

    def test_exit_closes_browsers_even_when_body_raises(self):
        site = FakeSiteClass()
        entry = FakeEntry(key="9001", url=URL_A)

        with _patch_site_for({ORG_A: site}):
            with pytest.raises(RuntimeError, match="本体側で失敗"):
                with BrowserFetcher() as fetch:
                    fetch(entry)
                    raise RuntimeError("本体側で失敗")

        assert site.browsers[0].exit_args == [(None, None, None)]

r"""browser_report/fetch.py — ブラウザ経由でレポートを 1 件取得する。

本体の ``download_scheduled()`` に渡す「1 件取りに行く関数」を実装する。
本体（Salesforceレポートダウンローダー）から外へ移したブラウザ取得の処理はここ
だけに置き、管理表・保存先・履歴・排他はすべて本体側に残す。

使い方は context manager 1 つ::

    from browser_report.fetch import BrowserFetcher

    with BrowserFetcher() as fetch:
        table = fetch(entry)          # 1 件取る。戻り値は本体の Table

**組織ごとにブラウザを使い回す。** ``site_for()`` が URL から引く組織（サイト
クラス）ごとにブラウザを 1 つだけ開き、開いた直後に ``login_with_credentials()``
を 1 回だけ実行する。同じ組織の 2 件目以降はログイン済みのブラウザをそのまま使う。

**失敗したブラウザは使い回さない。** ログインや取得で例外が出たら、そのブラウザを
閉じて使い回しの入れ物から外し、例外はそのまま呼び出し側へ上げる。次の同組織の
行では新しいブラウザを開き直してログインし直す（壊れた状態のブラウザを次の
レポートへ引き継がないため）。

**閉じるときは ``__exit__(None, None, None)`` を呼ぶ。** 例外情報を渡すと、正常終了
でも comken 側がエラー時のスクリーンショットを撮るため。

``selenium`` はブラウザ操作を実際にするときだけ必要なので、``site_for()`` の
import は関数の中に置く（``selenium`` が無い環境でもこのモジュールは import できる）。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Protocol

from comken.core.table import Table
from comken.toolbox.csv import CSV

if TYPE_CHECKING:
    # ``self._sessions`` の型だけ使う。``from __future__ import annotations`` で
    # 注釈を文字列扱いにしているので、``TYPE_CHECKING`` に閉じてよい（無いと
    # Python 3.13 以下で import 時に NameError になる）。
    # この基底クラスは selenium を import するので、実行時には読み込まない。
    from comken.toolbox.browser.sites.salesforce.base import SalesforceReportBrowser

logger = logging.getLogger(__name__)


class ReportEntry(Protocol):
    """本体の管理表の 1 行（``src.sheets.master.ReportEntry``）が満たす契約。

    本体の ``src`` パッケージは実行時に ``sys.path`` へ追加する（``browser_report.run``
    がその役を持つ）ため、このファイルでは import できない。使うのは ``key``
    （一時ファイル名）と ``url``（取得対象のレポート）だけなので、その 2 つだけを
    宣言する。
    """

    key: str
    url: str


class BrowserFetcher:
    """ブラウザで 1 件取得する呼び出し可能オブジェクト。

    本体の ``download_scheduled(fetch_browser=...)`` に、このインスタンスを渡す::

        with BrowserFetcher() as fetch:
            download_scheduled("ブラウザ取得", fetch_browser=fetch)

    ``with`` を抜けるときに、開いたブラウザを全部閉じる。
    """

    def __init__(self) -> None:
        # 組織（サイトクラス）→ 開いているブラウザ。1 実行につき組織ごとに 1 つ。
        self._sessions: dict[type[SalesforceReportBrowser], SalesforceReportBrowser] = {}

    def __enter__(self) -> BrowserFetcher:
        """``with BrowserFetcher() as fetch:`` の ``fetch`` はこのインスタンス自身。

        ``fetch(entry)`` で 1 件取れるようにしておく。
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """``with`` を抜けたら開いたブラウザを全部閉じる。

        ブラウザの ``__exit__`` には ``(None, None, None)`` を渡す。例外情報を渡すと
        正常終了でもエラー時のスクリーンショットが撮られるため。
        """
        self._close_browsers()

    def __call__(self, entry: ReportEntry) -> Table:
        """``entry`` をブラウザで取得して、本体と同じ ``Table`` を返す。

        Raises:
            Exception: ログイン・取得で出た例外をそのまま上げる（ブラウザを閉じる
                処理で元の失敗を隠さない）。
        """
        site_class = self._site_class(entry.url)
        browser = self._sessions.get(site_class)
        if browser is None:
            browser = self._open_browser(site_class)
        try:
            return self._export_to_table(browser, entry)
        except Exception:
            # 壊れた状態のブラウザを次のレポートへ引き継がないため、閉じて外す。
            # 閉じるときに起きた例外は元の失敗を隠すので、警告ログに留めて
            # 元例外を上げる
            self._discard_browser(site_class)
            raise

    def _site_class(self, url: str) -> type[SalesforceReportBrowser]:
        """URL から組織（サイトクラス）を引く。

        ``selenium`` を実際に使うときだけ読み込みたいので、import は関数の中に置く。
        """
        from comken.toolbox.browser.sites.salesforce import site_for

        return site_for(url)

    def _open_browser(self, site_class: type[SalesforceReportBrowser]) -> SalesforceReportBrowser:
        """組織ごとのブラウザを開いてログインする。

        ``__enter__()`` が失敗した場合は何も開いていないので、dict にも入れず
        閉じもしない（``__exit__`` も未呼び出し）。``__enter__()`` が成功した時点で
        dict に登録する。``login_with_credentials()`` が例外を出しても「失敗したら
        閉じて入れ物から外す」範囲に入るので、後続のレポートで「壊れたブラウザ」を
        再利用しない。
        """
        # ``__enter__()`` を直接呼ぶのは、ブラウザを ``self._sessions`` に乗せて
        # ``__exit__`` まで保持するため。``with`` 節の中だとスコープを抜けてしまう
        browser = site_class()
        browser.__enter__()
        self._sessions[site_class] = browser
        try:
            # ID/パスワードは DPAPI から自動入力、MFA の承認はスマホで待つ
            # （無人で承認されなければ LoginFailedError、認証情報が無ければ
            # CredentialNotFoundError。いずれも ComkenError 系なので本体の
            # 既存処理で次のレポートへ続行される）
            browser.login_with_credentials()
        except Exception:
            self._discard_browser(site_class)
            raise
        return browser

    def _export_to_table(self, browser: SalesforceReportBrowser, entry: ReportEntry) -> Table:
        """ブラウザにエクスポートさせ、その CSV を ``Table`` にして返す。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / f"{entry.key}.csv"
            dict(browser.export_reports({entry.url: tmp_path}))
            with CSV(tmp_path, read_only=True) as source:
                return source.read()

    def _discard_browser(self, site_class: type[SalesforceReportBrowser]) -> None:
        """ブラウザを閉じて、使い回しの入れ物から外す。"""
        browser = self._sessions.pop(site_class, None)
        if browser is None:
            return
        try:
            browser.__exit__(None, None, None)
        except Exception as close_exc:
            logger.warning("ブラウザを閉じる途中で例外が発生しました: %s", close_exc)

    def _close_browsers(self) -> None:
        """使い回していたブラウザを全部閉じる。

        取得中に例外が出たものは既に ``_discard_browser()`` で閉じているため、
        ここに残っているのは成功裡に使い終わったブラウザだけ。
        """
        for site_class in list(self._sessions):
            self._discard_browser(site_class)

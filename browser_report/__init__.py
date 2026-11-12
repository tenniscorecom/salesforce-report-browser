"""browser_report/ — Salesforce レポートをブラウザ経由で取得する。

このリポジトリがするのは **本体の取り残しをブラウザで取ることだけ** である。
管理表・保存先・履歴・排他は全部本体（Salesforceレポートダウンローダー）側にあり、
このリポジトリは本体の ``download_scheduled()`` に「1 件取りに行く関数」を渡すだけ。

本体（Report API / SOQL）を実行したあとにこのリポジトリを実行すると、
対象選定で選ばれた、当日まだ取れていない行が全部ブラウザで取られる。

    browser_report/
      fetch.py  ← BrowserFetcher（ブラウザで 1 件取る・組織ごとに使い回す）
      run.py    ← 実行の入口（main）
"""

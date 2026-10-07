# UIのカラーテーマ

ユーザー提供の「Warm Clay UI Theme」に基づき、暖かいオフホワイト／チャコールとテラコッタの配色を使う。回路エディタ、プラズマ概念図、入力・表・状態表示、結果のグラフを同じトークンで描画する。

画面上部の「外観」で「システム」「ライト」「ダーク」を選択する。初期値は「システム」で、OSの配色変更に追従する。明示したライト／ダークはOS設定より優先する。選択はブラウザの `localStorage` の `plasma-circuit.theme` に保存する。保存先を利用できない場合も、その画面では切替できる。共有DBや社員番号の設定とは独立している。

色の値は [theme.css](../frontend/src/theme.css) に集約し、ライトとダークで同じトークン名を使う。[theme.ts](../frontend/src/theme.ts) で初期化・OS追従・保存を扱い、Reactが描画する前に解決したモードを `data-theme` へ設定する。6色のグラフ系列もCSS変数から参照し、表示中の波形をテーマの切替に追従させる。

本文・入力文字には `text`、補足文字には背景との組合せを確認した `text-muted`、主ボタンには `accent-strong` / `accent-fg` を使う。表面色との組合せで読みやすさを失う状態色は、背景やアイコン・文字の役割を調整する。色だけで状態を表すことを避け、既存の状態名やアイコンを保つ。

ブラウザ確認は既存結果の読み込みだけを使い、DBに回路を保存したり新たな計算を投入したりしない。

```bash
cd frontend
npm ci
npm run build
cd ..
.venv/bin/python -m pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/check_ui_theme.py --base-url http://localhost:8080 --output reports/ui-theme
```

[READMEの仮想環境準備](../README.md#動作検証)を済ませ、Node.jsとnpm、Python用PlaywrightとChromiumを準備する。アプリが起動し、保存済みの過渡解析結果がある状態で実行する。

確認結果と画面は [UIテーマのレポート](../reports/ui-theme/report.md) にまとめる。

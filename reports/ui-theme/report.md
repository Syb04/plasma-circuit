# Warm Clay UIテーマの反映

実施日：2026-10-07。

ユーザー提供の `SKILL.md` のWarm Clay配色をReactアプリへ反映し、Dockerフロントエンドを更新した。ライトはオフホワイト、ダークはチャコール、アクセントはテラコッタとし、指定のトークン値を保持した。

画面上部の「外観」で「システム」「ライト」「ダーク」を切り替える。既定の「システム」はOSの変更に追従し、明示したライト／ダークはOS設定より優先する。選択はブラウザごとの `localStorage` に保存され、社員番号や回路保存の状態から独立している。

色の値は [theme.css](../../frontend/src/theme.css) に集約した。[theme.ts](../../frontend/src/theme.ts) が初回描画前の設定、OS追従、選択の保存を扱う。回路図・プラズマ概念図・入力・表・状態表示・グラフの6系列・軸・ツールチップ・範囲選択をCSS変数で描画する。入力文字と選択中の部品ラベルは背景とのコントラストを確認し、本文色へ調整した。

## 確認結果

- `npm run build` 成功。TypeScriptと本番ビルドを通過。従来からの500 kB超バンドルに関するVite警告は残る。
- Dockerフロントエンドの再ビルド・再起動後、`http://localhost:8080` で確認した。
- OS追従、明示モードによる上書き、ライト／ダークの再読み込み後の保存を確認。
- 1600×1000と390×844の両モードを確認し、ページの横はみ出しはなかった。
- 保存済みの過渡解析を読み込み、テーマ切替後の波形・軸・ツールチップ・範囲選択を確認した。
- ブラウザの実行時エラー0件。確認中のDB更新リクエスト0件。

背景色の既存150 ms遷移が終わってから画面を記録した。[機械検証JSON](verification.json)と[確認スクリプト](../../scripts/check_ui_theme.py)を保存した。

| 画面 | ライト | ダーク |
| --- | --- | --- |
| 回路エディタ | [PNG](light-editor.png) | [PNG](dark-editor.png) |
| 計算結果 | [PNG](light-results.png) | [PNG](dark-results.png) |
| ツールチップ | [PNG](light-tooltip.png) | [PNG](dark-tooltip.png) |
| モバイル | [PNG](light-mobile.png) | [PNG](dark-mobile.png) |

[変更前の画面](before.png)、[設定・再確認手順](../../docs/ui-theme.md)。

## Dockerの反映範囲

更新したフロントエンド画像は `sha256:f05119bca7845fc912a5c18f1d7aa4986b0a586661c157937b77200cf954b1f5`、ビルド出力は `index-qxKvFkfU.css` / `index-C4oCJey5.js`。API・workerの研究モデルはこのUI更新では再デプロイしていない。O₂の壁輸送改善は[研究ソルバーの比較レポート](../oxygen-transport-study/report.md)に記録している。

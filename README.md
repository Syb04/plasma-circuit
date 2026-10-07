# Plasma Circuit

Reactで回路を編集し、PySpice / ngspiceで計算する、Docker Compose対応の回路・プラズマ解析アプリです。回路の版と計算結果をPostgreSQLに保存します。

## 起動

Docker EngineとDocker Composeが必要です。

```bash
git clone https://github.com/Syb04/plasma-circuit.git
cd plasma-circuit
docker compose up --build -d
```

非公開リポジトリへのアクセス権が必要です。取得済みの場合は、プロジェクトのルートで `docker compose up --build -d` を実行します。

ブラウザで **http://localhost:8080** を開きます。初回はイメージと依存パッケージを取得します。ポートを変える場合は `.env.example` を `.env` にコピーして `APP_PORT` を変更してください。

```bash
docker compose logs -f api worker
docker compose down
```

`down` 後も回路・結果はDockerボリュームに残ります。`down -v` は保存データを削除します。

この開発用クラウド環境では、ビルド時の証明書をsecretとして渡す追加設定を使用します。

```bash
docker compose -f compose.yaml -f compose.cloud.yaml up --build -d
```

## 使い方

1. 「プリセットから始める」で分圧回路、RC、非線形EDD、各ガスのCCPを選びます。
2. 通常回路では部品を配置し、端子から端子へ配線します。部品を選ぶと値・モデル・EDD式を編集できます。
3. 解析種類と条件を設定し、社員番号を入力して保存・実行します。社員番号は文字列で、先頭の0も保存します。
4. 結果画面で波形、数値、計算条件、モデル、ネットリストを確認します。CSVをダウンロードできます。
5. 保存済み回路と計算履歴から再表示できます。他の人の更新と競合した保存は通知します。

認証はありません。社員番号は操作履歴の記録に用います。接続できるチームメンバーが回路と履歴を共有する構成です。

## 実装範囲

| 機能 | 内容 |
| --- | --- |
| 回路解析 | DC動作点、DCスイープ、AC小信号、過渡解析 |
| 基本素子 | R/C/L、相互結合、独立・従属電源、数式電源、スイッチ、ダイオード、BJT、JFET、MESFET、伝送線、サブ回路、接地・接続点 |
| EDD | 複数枝の導電電流 `I(V)` と電荷 `Q(V)`、枝間依存、パラメータ、中間式。端子電流は `I + dQ/dt` |
| CCP | 非線形シース電荷・電流とバルクR/Lをngspiceで過渡計算。周期平均電極電流0からDC自己バイアスを求める |
| グローバルモデル | 純Arの粒子・電子エネルギー収支とRF回路の定常連成。出典付き反応表を登録する拡張インターフェース |
| 保存 | 回路版、社員番号、実行時の回路・モデル・条件、ソルバー版・実装ハッシュ、圧縮した計算結果 |

MOSFET、混合ガス、空間分布の計算は初期版の対象外です。回路図の配線交差は電気的接続を意味せず、明示的につないだ端子だけを接続します。

### CCPの基準条件

| 入力 | 初期値 |
| --- | --- |
| 初期版の対象ガス | Ar / O₂、各単独。CF₄は採用保留 |
| RF | 40 MHz、駆動電極の正弦波ピーク250 V |
| カソード | 300 mmウェハー側 |
| 有効接地面積 / 有効駆動面積 | 5 |
| 圧力 | 10 mTorr = 1.333223684 Pa |
| 電極間隔 / ガス温度 | 50 mm / 300 K |
| 主な壁・電極材 | シリコン。表面状態・表面温度は未確定 |

駆動電極電圧は `Vdc + 250 sin(2π·40 MHz·t)` です。円形電極を仮定したカソード面積は約0.070686 m²です。体積と壁損失面積は独立に設定でき、未指定時の幾何学的仮定を結果に記録します。

CCPは専用の設定テンプレートです。通常回路図に追加した任意の部品との自動連成はまだ対応していません。EDDを使った通常回路の作成と解析は別に実行できます。

### 物理モデルの扱い

初期CCPモデルは、一様イオンのシースとDrudeバルクによる縮約モデルです。電子加熱はバルクの抵抗損失で、シース加熱・二次電子・空間分布を含みません。運動量衝突頻度は入力値です。実機の定量予測について検証したモデルではありません。

Arの標準グローバルモデルは基底状態の電離・励起を使います。求めるのはRF周期定常状態であり、放電着火や密度・温度の巨視的な時間発展ではありません。

O₂は固定密度・温度の等価回路を実行できます。グローバル解析には負イオン・解離種を含む出典付きの `reaction_model` と初期粒子密度の登録が必要です。O₂の文献由来の粒子計算用データを同梱していますが、電子エネルギー収支を含む標準O₂モデルの統合・バリデーションは未完了です。初期プリセット・新規ガス候補はArとO₂とし、保存済みCF₄の読み込みは維持します。詳細は [物理モデル](docs/plasma-models.md)、[O₂の文献調査・CF₄の保留方針](docs/oxygen-literature-review.md)、[検証状況](docs/validation-status.md) を参照してください。

## 構成

```text
ブラウザ → React / nginx → FastAPI → PostgreSQL
                                ↓
                              Redis → RQ worker → PySpice / ngspice
```

ソルバーはWeb APIと別のプロセスで実行します。停止・時間制限・計算エラーを履歴に残します。計算後に回路を編集しても過去の実行スナップショットは変わりません。

- `frontend/`：React、TypeScript、回路編集とグラフ
- `backend/app/engine.py`：回路図からngspiceへの変換・解析
- `backend/app/plasma.py`：CCP・定常グローバル連成
- `backend/app/api.py`、`database.py`、`worker.py`：API・保存・ジョブ管理
- [仕様](docs/specification.md) / [EDDの使い方](docs/edd.md) / [データ・API契約](docs/CONTRACT.md)

## 動作検証

Docker内で、数値解を含むバックエンドテストを実行できます。

```bash
docker compose run --rm --no-deps api python -m pytest -q
```

起動中のサービスに対して、社員番号の必須入力、回路保存、版競合、スナップショット、実ソルバー、結果保存、CSVを検証します。Python 3の標準ライブラリだけで実行できます。

```bash
python3 scripts/smoke.py --include-plasma --include-global
```

この検証はテスト用の回路・履歴をDBに作成します。数値テストの成功と、物理モデルの実験的な妥当性は別の検証です。

研究スクリプトをDockerの外で実行する場合は、Python 3.12の仮想環境と計算・描画用の依存パッケージを準備します。以下はプロジェクトのルートで実行します。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
```

O₂の損失・収支について、指定吸収電力の研究スクリプトでパラメータスタディを実行しました。[結果・図・CSV](reports/oxygen-parameter-study/report.md)を参照してください。これは明示した縮約モデル内の収支確認であり、Si電極CCPの検証済み標準モデルではありません。

圧力・電気陰性度に応じてhL/hRを計算する2000年の壁輸送近似も追加しました。[同条件での新旧比較・独立検算](reports/oxygen-transport-study/report.md)を参照してください。既存の固定h方式を維持し、2001年の空間閉包や低圧側の検証とは区別しています。

UIはWarm Clayのライト／ダーク配色に変更し、画面上部の「外観」からOS追従・ライト・ダークを選択できます。選択はブラウザごとに保存します。[設定と実装](docs/ui-theme.md)、[Docker画面・ブラウザ確認](reports/ui-theme/report.md)を参照してください。

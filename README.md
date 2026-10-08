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

1. 「プリセットから始める」で分圧回路、RC、非線形EDD、Ar/O₂のCCP、外部RF・整合・DCブロック付きCCPを選びます。
2. 通常回路では部品を配置し、端子から端子へ配線します。部品を選ぶと値・モデル・EDD式を編集できます。
3. 解析種類と条件を設定し、社員番号を入力して保存・実行します。社員番号は文字列で、先頭の0も保存します。
4. 結果画面で波形、数値、RF測定面、計算条件、モデル、収支残差、ネットリストを確認します。「CSV保存」「モデルパッケージ」で書き出せます。
5. 「研究・比較」で1〜2軸のパラメータ研究、失敗ケースの再開、2〜4結果の比較、参照CSV/JSONとの比較、パッケージの読込を行います。
6. 「保存済みモデル」の一覧ページで、モデル名・メモ・社員番号を検索できます。更新者・更新期間での絞り込み、並べ替え、25／50／100件ずつのページ表示に対応しています。編集中の内容を保持して一覧へ移動でき、保存回路と計算履歴から再表示できます。他の人の更新と競合した保存は通知します。

認証はありません。社員番号は操作履歴の記録に用います。接続できるチームメンバーが回路と履歴を共有する構成です。

## 実装範囲

| 機能 | 内容 |
| --- | --- |
| 回路解析 | DC動作点、DCスイープ、AC小信号、過渡解析 |
| 基本素子 | R/C/L、相互結合、独立・従属電源、数式電源、スイッチ、ダイオード、BJT、JFET、MESFET、伝送線、サブ回路、接地・接続点 |
| 素子パラメータ | `4e7`・`1e-9` の指数入力、詳細ダイオードのIS/N/RS/BV/IBV・接合容量・走行時間・温度依存。通常のモデル名参照も使用可能 |
| EDD | 複数枝の導電電流 `I(V)` と電荷 `Q(V)`、枝間依存、パラメータ、中間式。端子電流は `I + dQ/dt` |
| CCP・外部回路 | 非線形シースI/QとバルクR/Lをngspiceで過渡計算。2端子 `PLASMA`、電源抵抗、整合RLC、DCブロック、DC給電、2周波数・パルス包絡 |
| RF測定 | 電極側・電源側の大信号基本波Z、位相、RMS、平均電力、高調波、THD、指定実数Z₀での進行・反射電力 |
| 定常0D | 純ArとRF回路の連成。O₂の文献由来48粒子反応＋縮約エネルギー閉包を指定総吸収電力またはRF連成で実行。ユーザー反応表も登録可能 |
| 時間発展0D | Ar/O₂の粒子密度・電子エネルギー・ガス温度をBDFで実積分。指定総電力またはマクロ時刻ごとに再計算したRF平均電力を使用 |
| 任意の物理拡張 | 出典付き断面積/EEDFによるν積分、独立した電極・壁係数、縮約移動シース加熱と有効抵抗によるRF逆作用、二次電子、IEDF粒子軌道 |
| 径方向分布回路 | 同心円環の線形RF回路による電極電圧・位相・吸収電力分布。密度は一様な入力値 |
| 研究・比較 | 最大100ケースの直積スイープ、停止・再試行履歴、保存結果比較、出典・単位・不確かさ付き参照データ、ハッシュ検証付き解析パッケージ |
| 保存 | 回路版、社員番号、実行時の回路・モデル・条件、ソルバー版・実装ハッシュ、圧縮した計算結果 |

MOSFET、混合ガス、放電着火、PIC、自己無撞着な空間反応・粒子輸送、完全なBoltzmann/EEDF解は対象外です。回路図の配線交差は電気的接続を意味せず、明示的につないだ端子だけを接続します。

素子の指数入力と詳細ダイオードの設定手順・各パラメータは[回路素子のパラメータ](docs/circuit-parameters.md)を参照してください。

### CCPの基準条件

| 入力 | 初期値 |
| --- | --- |
| 初期版の対象ガス | Ar / O₂、各単独。CF₄は採用保留 |
| RF | 40 MHz、専用の理想駆動テンプレートでは電極側ピーク250 V |
| カソード | 300 mmウェハー側 |
| 有効接地面積 / 有効駆動面積 | 5 |
| 圧力 | 10 mTorr = 1.333223684 Pa |
| 電極間隔 / ガス温度 | 50 mm / 300 K |
| 主な壁・電極材 | シリコン。表面状態・表面温度は未確定 |

専用の理想駆動テンプレートでは `Vdc + 250 sin(2π·40 MHz·t)` を電極に与え、無DC給電の周期平均電極電流0からVdcを求めます。外部回路では250 Vは**電源側**のピーク値で、電極電圧は回路から求めます。DCブロックで絶縁された電極は容量の周期電荷平衡を求め、DC給電のある回路は接続に従う平均電流を許容します。

円形電極を仮定したカソード面積は約0.070686 m²です。体積・壁損失面積・O₂化学計算の円筒表面積は区別し、幾何学的仮定を結果に記録します。専用テンプレートには部品を追加できません。通常回路図では `PLASMA` を1個配置して外部素子を配線します。複数プラズマの同時連成は未対応です。

### 物理モデルの扱い

CCPの基礎モデルは一様イオンのシースとDrudeバルクです。既定の電子加熱はバルク抵抗損失、νは入力値、二次電子収率は0です。「電子輸送・EEDF・加熱（実験的）」「表面・材料条件」から任意の縮約モデルを選べます。断面積やSi表面係数をガス名・材料名から自動生成しません。

Arの定常グローバルモデルは基底状態の電離と集約した励起損失を使います。「プラズマ・時間発展0D」は別の解析で、既に粒子を含む準中性プラズマの密度・電子エネルギー・ガス熱収支を積分します。放電着火や係数の適用範囲外の消滅を予測するモデルではありません。

O₂は固定密度・温度のCCPと、組み込みの縮約グローバルモデルを実行できます。O₂のエネルギー閉包は48反応すべてのエネルギー移送を網羅せず、Si電極で検証した標準モデルではありません。既存のO₂表面候補値はSUS/Fe由来であり、Siへ読み替えません。初期プリセット・新規ガス候補はArとO₂とし、保存済みCF₄の読み込みは維持します。CF₄の採用・標準化学モデルは保留です。

**機能の利用可能性、数値収束、実験的な妥当性は別です。** `succeeded` はジョブ完了、`converged` は各モデルの数値診断です。実験検証済みという意味ではありません。詳細は [物理モデル](docs/plasma-models.md)、[解析・研究の操作例](docs/analysis-workflows.md)、[電子・表面モデル](docs/electron-surface-models.md)、[IEDF・径方向モデル](docs/ion-radial-models.md)、[検証状況](docs/validation-status.md) を参照してください。

## 構成

```text
ブラウザ → React / nginx → FastAPI → PostgreSQL
                                ↓
                              Redis → RQ worker → PySpice / ngspice
```

ソルバーはWeb APIと別のプロセスで実行します。停止・時間制限・計算エラーを履歴に残します。計算後に回路を編集しても過去の実行スナップショットは変わりません。

- `frontend/`：React、TypeScript、回路編集とグラフ
- `backend/app/engine.py`：回路図からngspiceへの変換・解析
- `backend/app/simulation.py`：解析の共通入口、電子・表面・IEDF・数値精細化の接続
- `backend/app/plasma.py`、`external_rf.py`：CCP・外部回路・定常グローバル連成
- `backend/app/oxygen_adapter.py`、`global_dynamics.py`：O₂縮約閉包・時間発展0D
- `backend/app/studies.py`、`benchmarks.py`、`analysis_package.py`：研究・参照比較・再現用パッケージ
- `backend/app/api.py`、`database.py`、`worker.py`：API・保存・ジョブ管理
- [仕様](docs/specification.md) / [EDDの使い方](docs/edd.md) / [解析・研究の操作例](docs/analysis-workflows.md) / [データ・API契約](docs/CONTRACT.md)

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

拡張機能の実キューAPI確認は [check_plasma_features.py](scripts/check_plasma_features.py)、研究・比較・参照・パッケージのブラウザ確認は [check_feature_ui.py](scripts/check_feature_ui.py) で実行できます。どちらも検証用データをDBへ保存します。

```bash
python3 scripts/check_plasma_features.py --base-url http://localhost:8080
.venv/bin/python scripts/check_feature_ui.py --base-url http://localhost:8080 --verify-existing-physics
```

ブラウザ確認にはPlaywrightとChromiumが必要です。上記の順でAPI確認を先に実行します。`--verify-existing-physics` は成功・数値収束したCCP＋IEDF、時間発展0D、径方向の3種類を必須として表示を確認します。[拡張機能の検証レポート](reports/simulator-extensions/report.md)に、最終Dockerテスト、実API結果、機能別の確認範囲、モデル適用範囲の警告を記録しています。最新のブラウザ確認状況もレポートを参照してください。

保存済みモデルの一覧・検索・ページ送り・コピー保存は `python3 scripts/check_model_library.py --base-url http://localhost:8080` で確認できます。27件の検証モデル、コピー・別クライアント保存、実計算1件をDBに作成します。[一覧ページの検証記録](reports/model-library/report.md)を参照してください。

指数入力・詳細ダイオードの保存と実計算は `python3 scripts/check_source_diode_ui.py --base-url http://localhost:8080` で確認できます。モデル1件と正弦波／パルスの実計算2件をDBに作成します。[今回の検証記録](reports/source-diode/report.md)を参照してください。

研究スクリプトをDockerの外で実行する場合は、Python 3.12の仮想環境と計算・描画用の依存パッケージを準備します。以下はプロジェクトのルートで実行します。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
```

O₂の損失・収支について、指定吸収電力の研究スクリプトでパラメータスタディを実行しました。[結果・図・CSV](reports/oxygen-parameter-study/report.md)を参照してください。これは明示した縮約モデル内の収支確認であり、Si電極CCPの検証済み標準モデルではありません。

圧力・電気陰性度に応じてhL/hRを計算する2000年の壁輸送近似も追加しました。[同条件での新旧比較・独立検算](reports/oxygen-transport-study/report.md)を参照してください。既存の固定h方式を維持し、2001年の空間閉包や低圧側の検証とは区別しています。

UIはWarm Clayのライト／ダーク配色に変更し、画面上部の「外観」からOS追従・ライト・ダークを選択できます。選択はブラウザごとに保存します。[設定と実装](docs/ui-theme.md)、[Docker画面・ブラウザ確認](reports/ui-theme/report.md)を参照してください。

# シミュレーター拡張の実装・数値・API検証

確認日：2026-10-07。単独Ar/O₂を対象とする回路・縮約プラズマモデルと研究ワークフローを実装し、最終Dockerバックエンド全体で391テストが合格した。実際のキューを通したAPI計算は5ケースすべてジョブ成功・数値収束。最終フロントエンドビルド、拡張機能11チェックと従来スモーク4チェックのブラウザ受入確認も合格した。

**機能の利用可能性、ジョブ完了、数値収束、モデル適用範囲、実験的な妥当性は別の結果である。** 本検証はSi電極・40 MHz装置の定量予測を検証したものではない。RF／径方向APIケースには数値収束と同時に電磁的適用範囲の警告が残っている。

## 保存した証跡とビルド

- [検証manifest](verification.json)：最終テスト結果、ビルド、Dockerイメージ、40ソースファイルのSHA-256。
- [実API計算の証跡](api-validation.json)：各run ID、状態、主要値、収支、RF測定面、精細化診断。
- [最終配備APIの入力拒否・health証跡](api-package-errors.json)：list／dict型の `hash_algorithm` に対する422応答、DB・キューのhealth。
- [実API確認スクリプト](../../scripts/check_plasma_features.py)：保存→キュー実行→結果取得を行う。
- [拡張機能ブラウザ証跡](../feature-ui/verification.json)：実サービスの11チェック、8ケースの停止・再開、3種類の保存済み物理run表示。
- [従来スモークのブラウザ証跡](../browser-smoke/verification.json)：社員番号入力、EDD、手配線回路、O₂指定電力計算、CSV、モバイルの4チェック。
- [ブラウザ確認スクリプト](../../scripts/check_feature_ui.py)と[従来スモーク](../../scripts/browser_smoke.py)：APIと計算結果をモックせずに確認する。

| 対象 | 最終結果 | 範囲 |
| --- | --- | --- |
| 最終Dockerバックエンド全体 | 391 passed、失敗0、警告1、176.22 s、終了コード0 | 数式の独立検算、入力拒否、実ngspice、保存・API・研究ワークフロー、ブラウザJSON数値のパッケージ検証、不正なハッシュ方式の型の拒否を含む |
| 最終フロントエンド | `npm run build`成功 | TypeScript検査と本番ビルド。O₂電力モード保存の修正を含む。既存のVite chunk-size advisoryあり |
| 実キューAPI計算 | 5/5 succeeded、5/5 converged、`passed=true` | 固定CCP＋IEDF＋精細化、外部RF、O₂指定総電力、Arパルス時間発展、径方向精細化 |
| 最終配備APIの入力拒否 | 2/2期待どおり422、`passed=true`、health ok | list／dict型の `hash_algorithm` を `Unsupported analysis package hash algorithm` で拒否。DB・キューok、PySpice 1.5 |
| 拡張機能ブラウザ受入確認 | 11チェック合格、`completed=true`、runtime error 0 | 実研究、停止・再開、比較、合成参照、パッケージ、条件表示、モバイル、既存の実物理run 3種類 |
| 従来ブラウザスモーク（O₂含む） | 4チェック合格、`completed=true`、runtime error 0 | 実EDD・手配線分圧回路・O₂指定吸収電力の3run、Q–V・CSV・履歴・モバイル |

バックエンドの警告1件はStarletteの非推奨anyio使用に関するもの。今回の `faulthandler_timeout=120` ではスタックダンプはなく、300秒のコマンド制限内で全テストが完走し、終了コード0となった。

```bash
docker compose -f compose.yaml -f compose.cloud.yaml run --rm --no-deps api timeout 300s python -X faulthandler -m pytest -q -o faulthandler_timeout=120
```

最終フロントエンド資産は `index-CNFsynoX.js`、`index-D7JUZkyy.css`。解析方法・研究などの選択欄のアクセシブル名、マクロOFF比が平均電力比であることを示す表記、指定吸収電力へ切り替える際に吸収電力が未設定なら500 Wを同時保存する修正を含む。既に入力した電力値は保持する。

| イメージ | SHA-256 |
| --- | --- |
| API / worker（最終配備・全テスト合格） | `4de7f9d0065ad36993ee9eaf9a76d88f6180f47166eb0bbc0b74497fd825e7f1` |
| API / worker（ブラウザ確認時） | `107490c4606ee648528ee4ccca7553b01c91aa75f2df047f2c8aa1cf60eb7fa1` |
| frontend | `d0a9afa8cc27bc945db69f61d1a639b6c0776982f71c7c5789ee128d13b8323b` |

実APIの5ケースはworkflow/APIのみのビルド更新前に実行し、物理ソルバーのソースはその後の試験イメージと同じである。ブラウザの2検査は上表のブラウザ確認時API/workerイメージで実行した。ブラウザ確認後に追加した、不正な `hash_algorithm` 型を422で拒否する修正は最終配備イメージの実HTTP 2件で確認した。最終バックエンド全テストの対象にも含め、ブラウザの正常系確認と区別する。実HTTPの2件は入力検査である。

## 完成した機能と検証範囲

すべての行は実装済みの機能を示す。バックエンドテストには解析解・合成入力・注入したRFソルバーを使う検査と、実ngspiceを使う検査が含まれる。これらを実験データと混同しない。実サービス列のAPI計算は5ケース、ブラウザ操作は下記の11＋4チェックに限る。

| 機能 | バックエンドの主な証跡 | 今回の実サービス証跡・制限 |
| --- | --- | --- |
| 通常回路・EDD・保存 | `test_engine.py`、`test_expressions.py`、`test_api.py`：実回路、I＋dQ/dt、入力検査、版競合、スナップショット、停止・時間制限 | ブラウザでEDD編集・計算・Q–V・CSVと手配線分圧回路・保存再読込を確認 |
| 固定CCP・Ar定常global | `test_plasma.py`、`test_external_rf.py`：実ngspiceの周期性・DC電流・RF電力・Ar収支、外部回路を各反復へ使用 | Ar固定CCP。定常ArのAPIケースは今回の5件には含まれない |
| 2端子PLASMA・外部回路 | `test_external_rf.py`：DC給電、50 Ω電源、整合、DCブロック、伝送線、周期電荷平衡 | 外部RFプリセットを実回路として実行。複数プラズマは拒否 |
| 2周波数・RFパルス包絡 | `test_external_rf.py`：共通周期と実RF波形、加熱逆作用との連成 | 単独の実APIケースはなし。包絡振幅比とマクロ電力比を区別 |
| RF測定 | `test_rf_analysis.py`：既知R/LのZ、進行・反射電力、DC／高調波、保存基本周期と刻み検査 | 固定CCPの電極測定と外部回路の電源側・電極側測定 |
| 出典付き断面積・EEDF輸送 | `test_electron_transport.py`：Maxwellian／表形式の解析積分、SI倍率、正規化、範囲・尾確率、入力拒否 | 単独の実APIケースはなし。物理断面積の既定データやBoltzmann解を提供しない |
| 独立電極・壁・二次電子 | `test_surface_models.py`、`test_electron_heating.py`：面積重み、感度と指定範囲、局所収率、単一計上 | 単独の実APIケースはなし。Si係数の較正・推定はしていない |
| 移動シース加熱・RF逆作用 | `test_electron_heating.py`、`test_simulation_extensions.py`：Maxwellian反射積分、圧力仕事の分離、実RF有効抵抗の固定点 | 単独の実APIケースはなし。縮約抵抗による周期平均閉包 |
| O₂縮約定常0D・RF閉包 | `test_oxygen_adapter.py`、`test_simulation_extensions.py`：指定総電力、実ngspiceの縮約RF電力、粒子・壁電流・有効質量 | 指定総吸収500 Wの実API。ブラウザでも指定電力の保存・計算・表・CSVを確認。O₂ RF連成は今回のAPIケース外 |
| Ar/O₂時間発展0D・ガス熱 | `test_global_dynamics.py`：実BDF、粒子・電荷・原子・積分エネルギー、パルス・熱的極限・係数範囲の終了 | Ar指定電力パルスを実積分。O₂時間発展は今回のAPIケース外 |
| マクロRF再評価・パルス写像 | `test_global_dynamics.py`、`test_simulation_extensions.py`：注入した搬送ソルバーによるsample/hold、包絡の一度だけの適用、電源包絡引継ぎ | マクロRF連成の実APIケースはなし。OFF振幅二乗は明示した二次電力近似 |
| IEDF粒子軌道 | `test_ion_transport.py`、実RFラッパー：DC解析解、RF通過、刻み、seeded電荷交換、未解決粒子、共通beat周期 | Ar固定CCPから実シース波形を渡しIEDF収束を確認。保存済み実runのヒストグラムをブラウザ表示 |
| 径方向分布回路 | `test_radial_model.py`：一様極限、KCL・電力、固定給電幅のメッシュ比較と範囲警告 | 12→24セルの精細化と保存済み実runのブラウザ表示。密度は一様な指定値 |
| 数値精細化 | `test_simulation_extensions.py`：基準未収束や不適切な指定電力RF精細化を合格にしない | CCP 256→512点と径方向12→24セルを比較。物理validationはfalse |
| 研究・停止・再試行・波形軸 | `test_workflows.py`：最大100ケース、1〜2軸、不変入力、履歴、キュー失敗、6数値波形selectorと無効RF軸拒否 | ブラウザで抵抗×sin周波数の4ジョブと8ケース停止・再開を確認。任意JSON・モデル式を軸にしない |
| 保存結果・参照CSV比較 | `test_workflows.py`：共通RF周期、マクロ除外、解決済み条件、単位、数値overflow、OP指標 | ブラウザで保存波形・条件差、OP／過渡比較、合成参照CSVを確認。参照fixtureの一致を実験検証としない |
| 解析パッケージ | `test_workflows.py`：model_metadataハッシュ、形式・サイズ・改変拒否、来歴と新規回路 | ブラウザで実出力の保存・アップロード・インポートを確認。整合性検証と発行者認証を区別し再計算を要求 |

テストファイルは [backend/tests](../../backend/tests)、式・設定は[モデル仕様](../../docs/plasma-models.md)、操作は[解析ワークフロー](../../docs/analysis-workflows.md)を参照。

## ブラウザで確認した範囲

拡張機能の11チェックはAPI・計算結果ともモックを使わず完走し、runtime errorは0件。抵抗100／180 Ωとsin電源1000／2000 Hzの2軸研究で4ジョブが成功し、1000 Hzの2結果について数値収束を明示検査した。8ケースが停止状態となり、再開後は8ジョブ成功、試行履歴が増えることを確認した。[研究画面](../feature-ui/studies-live.png)と[研究CSV](../feature-ui/study.csv)を保存した。

[保存波形比較](../feature-ui/compare-live-waveforms.png)と[OP／過渡の比較](../feature-ui/compare-live-null-axis.png)、[合成参照CSV比較](../feature-ui/reference-live-synthetic-benchmark.png)、[実パッケージ](../feature-ui/analysis-package.json)の保存・アップロード・読込を確認した。参照は実OP結果から作った非実験fixtureで、偏差0は操作確認の結果である。読込は `hashes_verified=true`、`authenticity_verified=false`、`requires_recalculation=true`、新規結果なしを確認した。

指定電力O₂、指定電力マクロ0D、径方向、IEDFの条件表示を確認し、390×844の[モバイル条件画面](../feature-ui/controls-mobile.png)に横溢れがなかった。さらに、次の3種類の成功・数値収束した既存API runを保存回路と履歴から開き、通常表示とダーク表示を保存した。これは既存の実計算結果の表示確認であり、ブラウザからこれら3モデルを新規計算した証跡ではない。

| 既存の実物理run | run ID | 画面 |
| --- | --- | --- |
| CCP＋IEDF | `001af305-0a3c-4a52-9509-f688dbdacda4` | [通常](../feature-ui/result-live-iedf.png)・[ダーク](../feature-ui/result-live-iedf-dark.png) |
| Ar時間発展0D | `8a66ce44-9820-41ff-9f39-933825ae64c8` | [通常](../feature-ui/result-live-global_transient.png)・[ダーク](../feature-ui/result-live-global_transient-dark.png) |
| 径方向 | `33565de4-be94-40ca-af83-377004f05960` | [通常](../feature-ui/result-live-radial.png)・[ダーク](../feature-ui/result-live-radial-dark.png) |

従来スモーク4チェックも実サービスで完走し、runtime errorは0件。[EDDのQ–V](../browser-smoke/edd-qv.png)、[手配線分圧回路](../browser-smoke/constructed-circuit.png)、[O₂指定電力の実結果](../browser-smoke/oxygen-0d.png)とCSV、保存再読込・履歴・390×844のモバイルを確認した。3runのIDは従来スモーク証跡に保存した。社員番号の必須入力確認は利用者の認証を意味しない。

## 5ケースのAPI結果

| ケース | 主要結果 | 数値診断 |
| --- | --- | --- |
| `ccp_iedf_refinement` | Ar、40 MHz、電極側250 Vpeak、24周期、256点。Vdc −213.254 V、電子加熱9.19781 W、電極吸収56.5025 W | RF電力相対残差0.782714%、IEDF収束。512点との比較を相対許容5%で合格 |
| `external_rf` | 外部プリセット、32周期、256点。Vdc −15.9992 V、電極吸収4.86982 W、電子加熱0.273289 W | RF電力相対残差0.00853378%。電源ポートZ ≈0.438115＋j15.7985 Ω |
| `oxygen_absorbed` | 500 W、600 K、半径0.152 m、長さ0.076 m、明示h=0.2。Te 3.538527 eV、ne 8.572558×10¹⁶ m⁻³、縮約損失500 W | 最大相対収支残差1.29818×10⁻¹³。RF信号を作らない |
| `argon_pulse_gas_heat` | 500 W、100 kHz、duty 0.5、OFF電力比0.5、10 µs。吸収エネルギー3.75 mJ、最終Tg 300.02245 K | BDFで要求時刻まで積分。全エネルギー残差7.77915×10⁻¹⁸ J |
| `radial_refinement` | RFピーク5 V、12セル。電子吸収0.006868666 W、シート損失0.001773967 W、入力0.008642633 W | KCL相対残差1.51232×10⁻¹³、電力相対残差3.24053×10⁻¹²。24セルとの比較を相対許容10%で合格 |

精細化の5%／10%はこの検証ケースの明示許容差であり、既定値2%や物理予測誤差を意味しない。固定CCPの電子加熱差は256→512点で約1.93%。外部RFの電源ポート進行156.250 W、反射151.379 W、正味4.87102 Wは同じ指定Z₀測定面の値で、電極側電力やイオン加速を同一の量として扱わない。

`ccp_iedf_refinement`、`external_rf`、`radial_refinement` の3ケースは、軸方向バルク長が0.3 Drude skin depthを超えるため `model_domain_valid=false`。一様軸方向電流近似が電磁的浸透構造を省略するという警告である。閾値自体も明示した工学的screenで、実験により保証された境界ではない。小さい数値残差や精細化合格によって、この警告を取り消さない。

## O₂と各縮約モデルに残る制限

既存の[O₂パラメータ研究](../oxygen-parameter-study/report.md)は121入力中118で縮約収支が整合し、独立コードで検算した。[輸送比較](../oxygen-transport-study/report.md)は44入力中38で整合し、1 mTorrの新旧計6入力は温度上限で失敗した。今回のWeb/API接続によって、これらの物理的未解決事項が解消したとは主張しない。

- k49は電荷保存の裏付けがないため除外し、原文49反応の完全再現ではない。
- 48粒子反応と16励起項を接続したが、励起標的電離・脱離・超弾性・付着・追加解離などの完全な反応エネルギー表と電子出生エネルギーは未完了。
- 2000年の近似壁輸送は使えるが、2001年core/edge閉包、原文の弾性係数の確認、複数圧力での文献密度・温度の再現は未完了。
- 1 mTorr側、13.56 MHzの文献CCP比較、40 MHz・面積比5・Siの適用確認は未完了。既存のSUS/Fe表面候補はSi係数ではない。
- Maxwellian／輸入EEDFの輸送積分はBoltzmann方程式の解ではない。輸送EEDFだけでは既存化学係数を自動置換しない。
- 移動壁・二次電子は縮約閉包。IEDFは固定幅・一様電場。径方向回路は一様密度入力で、自己無撞着な空間反応輸送・PIC・完全電磁界ではない。
- マクロ0Dは既に粒子を含む準中性状態の発展で、着火、係数範囲外の消滅、壁温度・完全な化学エンタルピーを解かない。
- CF₄の採用・標準化学モデル、混合ガス、実験による較正は保留／範囲外。

## 再実行

起動済みサービスで実API確認を行う。検証用回路と履歴をDBへ作る。

```bash
python3 scripts/check_plasma_features.py --base-url http://localhost:8080 --output reports/simulator-extensions/api-validation.json
```

PlaywrightとChromiumを利用できる環境でブラウザ確認を行う。`--verify-existing-physics` は成功・数値収束した既存のCCP＋IEDF、時間発展0D、径方向の3種類を必須とし、その実結果を表示する。先に `check_plasma_features.py` を実行して必要なrunを作る。3種類がそろわない場合は検査を失敗させる。表示したrun IDを証跡に記録し、ライト／ダークの画面を保存する。

```bash
.venv/bin/python scripts/check_feature_ui.py --base-url http://localhost:8080 --output reports/feature-ui --verify-existing-physics
```

従来スモークのO₂確認も再実行できる。

```bash
.venv/bin/python scripts/browser_smoke.py --base-url http://localhost:8080 --include-oxygen
```

合成参照fixtureのゼロ偏差、パッケージチェックサムの合格、数値収束から実験validationを宣言しない。

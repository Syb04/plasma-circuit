# 解析・研究ワークフロー

以下は現在のUI/APIと `app.simulation.execute_simulation` の操作例である。入力例は実行用の設定であり、装置の較正値や数値収束・実験一致を保証する条件ではない。既存の文献研究を再現するときは、そのレポートの幾何学・温度・表面係数を使い、40 MHz・Siの基準ケースと区別する。

## 最初の計算と結果の読み方

1. 「プリセットから始める」でAr/O₂ CCP、または「Ar CCP — 外部RF・整合・DCブロック」を開く。
2. 「解析種類」で固定CCP、定常global、時間発展0D、径方向回路を選ぶ。社員番号は先頭の0を保持する文字列で、保存・実行に必須。
3. 右側の条件と詳細項目を設定し「計算を実行」を押す。現在の編集を保存してから実行する。
4. 結果のモデル名・出典・仮定、`converged`、収支残差、適用範囲診断を読む。ジョブの完了とモデルの数値収束は別。
5. 「CSV保存」で保存サンプル／定常表、「モデルパッケージ」で入力と来歴を含むJSONを書き出す。

| 操作 | UIの場所 | 主な結果 |
| --- | --- | --- |
| 外部RLC・2周波数・包絡 | 外部回路・RF駆動 | 電極電圧、平均DCバイアス、電源ポートと電極の波形 |
| 輸送データの読込 | 電子輸送・EEDF・加熱（実験的）→出典付き輸送データJSON | 積分ν、各標的の衝突頻度、EEDFと範囲・出典 |
| 独立した材料係数 | 表面・材料条件→駆動電極／接地電極／チャンバー壁 | 面積・出典・範囲、壁閉包、局所二次電子 |
| 移動シース加熱 | 電子加熱モデル→移動シース境界・Maxwellian | 不可逆加熱推定、有効抵抗RF反復、電力予算 |
| O₂壁輸送 | O₂反応・中性ガス輸送 | 明示hまたはGudmundsson (2000)閉包と範囲診断 |
| マクロODE・ガス熱 | 時間発展・ガス加熱 | 実積分したn、Te、Tg、圧力、積分エネルギー残差 |
| IEDF | イオンエネルギー分布（IEDF・実験的） | 入射エネルギーPDF、種別の到達率・未解決率 |
| 精細化 | 数値収束・精細化検証 | 基準指標と追加計算との差、失敗理由 |
| RF測定 | 結果→RF診断・測定面 | 大信号Z・位相・RMS・電力・高調波・THD |

材料名Siだけでは反応係数は決まらない。3面すべてに値と出典または明示した仮定を入力する。出典付き断面積も同様で、アプリは物理データの仮値を生成しない。入力形式は[電子・表面モデル](electron-surface-models.md)を参照。

## 外部回路とRF波形

専用CCPの解析設定JSONに次を指定すると、50 Ω電源抵抗・100 nH直列L・1 nF DCブロック・10 pF電極側並列Cを実回路として計算する。250 Vは理想電源のピークで、電極振幅ではない。数値は外部回路プリセットの例であり、整合済みの装置を表すものではない。

```json
{
  "kind": "ccp",
  "settings": {
    "gas": "Ar", "frequency_hz": 40000000, "rf_peak_voltage": 250,
    "pressure_pa": 1.333223684, "gap_m": 0.05,
    "gas_temperature_k": 300, "cathode_diameter_m": 0.3, "area_ratio": 5,
    "electron_density_m3": 1e16, "electron_temperature_ev": 3,
    "cycles": 80, "points_per_cycle": 256,
    "external_circuit": {
      "source_resistance_ohm": 50, "series_inductance_h": 1e-7,
      "dc_block_capacitance_f": 1e-9, "shunt_capacitance_f": 1e-11,
      "dc_voltage_v": 0, "voltage_definition": "source",
      "reference_impedance_ohm": 50
    }
  }
}
```

通常回路図を使う場合は `external_circuit` を設定せず、2端子 `PLASMA` を1個と外部素子を配線する。電圧源の「電源波形・2周波数RF」で `rf` を選び、回路図側の波形を指定する。複数電源なら解析JSONの `rf_source_id` を指定する。

2周波数と周期包絡の追加設定例は次のとおり。主40 MHz、第2 20 MHz、包絡5 MHzは共通基本5 MHzを持つ。このRFのOFF比は**電圧振幅比**である。

```json
{
  "second_frequency_hz": 20000000,
  "second_rf_peak_voltage": 50,
  "second_phase_deg": 90,
  "fundamental_frequency_hz": 5000000,
  "pulse_frequency_hz": 5000000,
  "pulse_duty_cycle": 0.5,
  "pulse_off_fraction": 0.1
}
```

全周波数は共通基本の整数倍で指定する。低い包絡周波数と高い搬送波を同時にRF過渡計算すると要求点数が増え、250000点の上限や時間制限に達する。遅い密度・温度のパルス応答にはマクロ0Dを使い、RF再評価間隔の収束を別に確認する。

出力抵抗の負荷側がプリセットの電源測定面で、電極測定面とは異なる。「進行波電力」「反射波電力」は指定実数Z₀の面でDCを除いて算出する。「平均実電力」と「基本波電力」、基本波ピークと総RMSを混同しない。

移動シース加熱を選ぶ場合はRF解析設定へ次を追加する。反射確率1はモデルの入力仮定。共通実行入口は周期平均の有効抵抗を回路へ戻して反復するため、加熱推定だけでなく固定点残差と電力予算を読む。

```json
{
  "electron_heating": {
    "mode": "moving_wall_maxwellian", "reflection_probability": 1,
    "max_rf_iterations": 12, "budget_relative_tolerance": 0.01
  }
}
```

## O₂の指定総吸収電力

「プラズマ・グローバル解析」でO₂を選び、「電力の与え方」→「総吸収プラズマ電力を指定」を選ぶ。RF波形を生成しない定常化学計算である。以下の表面候補は既存研究と同じSUS/Fe由来で、Si用係数ではない。

```json
{
  "kind": "global",
  "settings": {
    "gas": "O2", "chemistry_model": "oxygen_reduced",
    "power_mode": "prescribed_absorbed", "absorbed_power_w": 500,
    "pressure_pa": 1.333223684, "gas_temperature_k": 300,
    "cathode_diameter_m": 0.3, "gap_m": 0.05,
    "flow_sccm": 50, "transport_mode": "explicit_h",
    "axial_edge_factor": 0.5, "radial_edge_factor": 0.5,
    "diffusion_o_m2_s": 1.2, "diffusion_o2_m2_s": 0.84,
    "electron_momentum_nu_s": {"O": 10000000, "O2": 10000000}
  }
}
```

RFと連成する場合は `power_mode=rf_coupled` としてRF・外部回路条件を追加する。48粒子反応＋16励起損失項を含む縮約エネルギー閉包であり、全反応エネルギー・Siでの実験精度は未検証。化学収支、反応係数範囲、輸送範囲、RF連成残差を別々に読む。

Docker起動後、次のコマンドは同じWeb実行入口から指定電力O₂を実行する。DBに保存するAPI操作とは別のローカル計算である。

```bash
docker compose exec -T api python - <<'PYCODE'
from app.presets import get_presets
from app.simulation import execute_simulation
preset = next(p for p in get_presets()["presets"] if p["id"] == "ccp-o2")
analysis = {"kind": "global", "settings": {
    **preset["analysis"]["settings"],
    "chemistry_model": "oxygen_reduced",
    "power_mode": "prescribed_absorbed", "absorbed_power_w": 500
}}
result = execute_simulation(preset["document"], analysis)
print(result["converged"], result["summary"])
print(result["diagnostics"]["oxygen_balances"])
PYCODE
```

## 時間発展0Dとガス熱

以下はArの既に粒子を含む状態からの時間積分例。マクロのOFF比0.5は**平均電力比**で、RF包絡電圧比ではない。ガス壁コンダクタンスと非弾性／イオン移送率は装置の測定値ではなく、明示した入力仮定として扱う。

```json
{
  "kind": "global_transient",
  "settings": {
    "gas": "Ar", "power_mode": "prescribed_absorbed",
    "absorbed_power_w": 500, "stop_time_s": 0.00001,
    "output_points": 201, "macro_relative_tolerance": 0.000001,
    "initial_electron_density_m3": 1e16,
    "initial_electron_temperature_ev": 3,
    "initial_gas_temperature_k": 300,
    "background_gas_temperature_k": 300,
    "gas_wall_conductance_w_k": 0,
    "gas_inelastic_heating_fraction": 0, "gas_ion_heating_fraction": 0,
    "pulse_frequency_hz": 100000, "pulse_duty_cycle": 0.5,
    "pulse_off_fraction": 0.5
  }
}
```

`n(e)`、`Te`、`Tg`、圧力、電子・ガス・全積分エネルギー残差と `diagnostics.termination` を確認する。ArはTe=1〜7 eV、O₂は1〜4.5 eVの境界で止まり、部分履歴を返す。着火や係数範囲外の消滅を予測しない。

RF連成では `power_mode=rf_coupled`、例えば `rf_update_interval_s=0.000002` を指定する。設定する再評価間隔は10共通搬送周期以上、パルス境界を含む合計区間数は200以下。パルス境界や最後の端数で短い実区間ができる場合も、RF平均とマクロ応答の時間尺度を確認する。再評価ごとに実RFを解くため計算は重い。より短い再評価間隔と比較する。通常回路図の電源包絡をマクロへ引き継ぐ場合、OFF振幅比を二乗して電力比へ変換する二次電力近似をmetadataへ記録する。これはOFF状態を別にRF計算した値ではない。異なる電源包絡には明示したマクロ包絡が必要。O₂で初期粒子密度を省略すると初期Tg・指定総電力の数値定常解を初期化に使う。明示初期密度は全動的種と準中性条件を満たす必要がある。

## IEDF・径方向分布・数値精細化

RF波形を持つCCPまたは定常globalでは解析設定へ次を追加する。

```json
{
  "iedf": {"enabled": true, "particles_per_species": 512,
           "histogram_bins": 80, "steps_per_rf_period": 256,
           "steps_per_transit": 64, "seed": 17,
           "charge_exchange_cross_section_m2": 0},
  "numerical_validation": {"enabled": true, "refine_points": true,
                           "refine_cycles": true, "relative_tolerance": 0.02}
}
```

断面積0は衝突なしの比較条件。正の値を使う場合はガス名から推定せず、出典と一定値近似を明示する。IEDFは固定幅・一様電場の軌道計算で、自己無撞着なシースではない。未到達・未解決粒子、PDF積分、統計量、粒子数と刻みへの依存を確認する。IEDFの収束は `result.iedf.converged` に独立して記録し、未収束は親結果にも反映する。

径方向解析は次の設定で実行できる。これは縮約回路の入力例で、シート値やνは較正した装置係数ではない。小信号・電磁的適用範囲の警告は計算成功と別に確認する。

```json
{
  "kind": "radial",
  "settings": {
    "gas": "Ar", "frequency_hz": 40000000, "rf_peak_voltage": 250,
    "cathode_diameter_m": 0.3, "gap_m": 0.05,
    "electron_density_m3": 1e16, "electron_temperature_ev": 3,
    "momentum_collision_frequency_hz": 10000000,
    "ion_density_m3": 1e16,
    "mean_cathode_sheath_voltage_v": 100,
    "mean_anode_sheath_voltage_v": 30,
    "radial_cells": 32, "radial_feed": "center",
    "electrode_sheet_resistance_ohm": 0.02,
    "electrode_sheet_inductance_h": 2e-9
  }
}
```

「給電位置」「径方向セル数」「電極シート抵抗」「電極シートL」を変えて電圧と電力分布を比較する。密度は一様入力で、密度分布を解かない。数値精細化はセル数を増やしてKCL・電力収支と指標を比較する。式と診断の意味は[IEDF・径方向モデル](ion-radial-models.md)を参照。

精細化は基準結果を保持する。RF点数／周期数、径方向メッシュ、ODE許容差の追加計算が失敗した場合や、上限のため比較可能な条件がない場合は合格にしない。実験的な妥当性は参照データで別に評価する。

## パラメータ研究と結果比較

「研究・比較」→「パラメータ研究」で1〜2軸を選ぶ。「ガス圧力（mTorr）」や「RF周波数（MHz）」はUI表示単位で入力し、保存する座標はSI値になる。APIでは最初からPa・HzなどのSIを使う。2軸は直積で、合計最大100ケース。

`POST /api/studies` は保存済み回路のID・版と、単一runと同じanalysisを使う。次は10/20 mTorr×200/250 Vの4ケースを表す。`circuit_id` と版は実際の保存値に置き換える。

```json
{
  "employee_id": "001234", "name": "Ar pressure-voltage study",
  "circuit_id": "saved-circuit-id", "expected_revision": 1,
  "analysis": {"kind": "ccp", "settings": {"gas": "Ar"}},
  "axes": [
    {"path": "analysis.settings.pressure_pa", "values": [1.333223684, 2.666447368]},
    {"path": "analysis.settings.rf_peak_voltage", "values": [200, 250]}
  ]
}
```

対応する数値設定、既存の数値部品パラメータ、または次の電源波形フィールドを変更する。波形のパスは `document.components.<id>.parameters.waveform.<field>` で、フィールドが既に数値として保存されている必要がある。

| 電源波形 | 対応フィールド |
| --- | --- |
| `sin`（V/I電源） | `frequency`、`amplitude` |
| `rf`（V電源） | `frequency_hz`、`rf_peak_voltage`、`second_frequency_hz`、`second_rf_peak_voltage` |

例えば電源IDが `rf_source` なら、`document.components.rf_source.parameters.waveform.rf_peak_voltage` を軸として指定する。通常回路図のPLASMA解析では電源波形が駆動条件を決めるため、無視される `analysis.settings.frequency_hz` や `analysis.settings.rf_peak_voltage` の軸はエラーにし、電源波形パスを案内する。空の専用テンプレートでは解析設定のRF軸を使う。径方向解析は独立した解析設定を使うため、このPLASMA電源による制限を受けない。上記以外の波形フィールドや任意の入れ子JSONはスイープで書き換えられない。

「研究を停止」は未完了runを停止する。「未完了ケースを再開」は失敗・時間超過・停止したケースの新しい試行を作る。同じ入力スナップショットを使い、過去のエラーや結果を上書きしない。完了ケースは再計算しない。「研究CSV」は最新試行の座標・状態・エラー・主要値を返し、全試行履歴は研究詳細で読む。

「結果比較」で2〜4保存結果を選び、入力差と主要値・波形を確認する。RF位相合わせは対応する保存RF波形の最後の完全周期を使う。マクロ時間履歴を比較するときは「RFの基本波位相を合わせる」を外して元の時間軸で比較する。径方向や定常表など必要なRF時間サンプルがない場合は、位相合わせできない理由を表示して元の軸を保持する。

## 参照CSV/JSONとパッケージ

「参照データ」では参照名、材料、測定定義、RF周波数、圧力、出典を必須入力する。CSVは `metric,value,unit,uncertainty`、JSONは `metrics` 配列。metricは保存結果の指標名に合わせる。次は列の説明用の合成参照であり、実験データではない。

```csv
metric,value,unit,uncertainty
dc_self_bias_v,-100,V,5
electron_density_m3,10000000000,cm^-3,1000000000
```

単位が対応すればSIへ明示変換する。例えばcm⁻³→m⁻³は1e6倍。不確かさは任意で、未指定を0や既定範囲にしない。参照との符号付き差、絶対差、相対差、不確かさ内かを返す。0の参照値には相対差を作らない。材料・条件・測定面・ピーク/RMS・総電力/基本波の定義を照合して解釈する。数値差が小さいことはモデル全体の実験検証完了を意味しない。

「モデルパッケージ」は入力・モデル・出典・実装ハッシュ・結果をまとめたJSON。`POST /api/packages/import` は社員番号とpackageを受け、形式、スキーマ、内容ハッシュを検証して新規回路と来歴を保存する。結果は過去の計算記録で、新しい成功runを作らない。読込後に再計算する。ハッシュ検証は発行者の認証や実験妥当性の保証ではない。

APIの全フィールドと制限は[CONTRACT](CONTRACT.md)、物理モデルの式と出典は[プラズマモデル](plasma-models.md)、試験の証跡は[検証状況](validation-status.md)を参照する。

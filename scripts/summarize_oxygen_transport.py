#!/usr/bin/env python3
"""Summarize exported transport comparisons, excluding failed candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT/"reports/oxygen-transport-study/results.json")
    parser.add_argument("--output", type=Path, default=ROOT/"reports/oxygen-transport-study")
    args = parser.parse_args()
    export = json.loads(args.input.read_text())
    records = export["cases"]
    unique = {case["input_sha256"]: case for case in records}
    successes = [case for case in unique.values() if case["result"].get("success")]
    failed = [case for case in unique.values() if not case["result"].get("success")]
    by_id = {case["case_id"]: case for case in records}
    old = by_id["paired-10mtorr-500-explicit_h"]["result"]
    new = by_id["paired-10mtorr-500-gudmundsson_2000"]["result"]
    assert old["success"] and new["success"]
    references = {(point["pressure_mtorr"], point["power_w"]): point["value"]
                  for point in export["metadata"]["references"]}
    comparisons = []
    comparison_lines = []
    for power in (100, 500, 1500):
        for mode in ("explicit_h", "gudmundsson_2000"):
            result = by_id[f"paired-10mtorr-{power}-{mode}"]["result"]
            assert result["success"]
            density = result["densities_m3"]["O2+"]
            reference = references.get((10, power))
            deviation = density/reference-1 if reference is not None else None
            comparisons.append({"power_w": power, "mode": mode,
                "te_ev": result["temperature_ev"], "ne_m3": result["electron_density_m3"],
                "o2plus_density_m3": density, "reference_density_m3": reference,
                "reference_deviation_rel": deviation})
            name = "旧：h固定0.2" if mode == "explicit_h" else "新：2000年輸送近似"
            diff = f"{100*deviation:+.2f}%" if deviation is not None else "比較点なし"
            comparison_lines.append(f"| {power} | {name} | {result['temperature_ev']:.4f} | "
                f"{result['electron_density_m3']:.5e} | {density:.5e} | {diff} |")
    residuals = {name: max(abs(case["result"]["residuals"][name]) for case in successes)
                 for name in ("energy_relative", "neutral_pressure_relative",
                              "oxygen_atoms_relative", "charge_relative")}
    residuals["species_relative"] = max(abs(value) for case in successes
        for value in case["result"]["residuals"]["species_relative"].values())
    cross = [case["result"] for case in records if case["family"] == "cross"]
    assert all(case["success"] for case in cross)
    transport = new["transport"]
    loss_rows = []
    for key, label in (("ionization", "電離"), ("excitation", "励起（表に含む解離を含む）"),
                       ("elastic", "弾性衝突"), ("electron_wall", "電子の壁損失"),
                       ("ion_wall", "イオンの壁損失"), ("total", "合計")):
        loss_rows.append(f"| {label} | {old['loss_w'][key]:.4f} | {new['loss_w'][key]:.4f} |")
    summary = {"unique_inputs": len(unique), "rows": len(records),
        "numerically_converged": len(successes),
        "failed_case_ids": [case["case_id"] for case in failed],
        "maximum_relative_residuals": residuals, "paired_10mtorr": comparisons,
        "new_baseline": {key: new[key] for key in ("temperature_ev", "electron_density_m3",
            "electronegativity", "loss_w", "transport")},
        "cross": {"cases": len(cross),
            "ne_range_m3": [min(r["electron_density_m3"] for r in cross), max(r["electron_density_m3"] for r in cross)],
            "te_range_ev": [min(r["temperature_ev"] for r in cross), max(r["temperature_ev"] for r in cross)]},
        "interpretation": "Numerical conservation and descriptive comparison to source model predictions; not experimental or Si-CCP validation"}
    report = f"""# O₂の圧力・電気陰性度依存の壁輸送モデル

実施日：2026-10-07。

## 結果と判断

固定のイオン壁損失係数hL/hRに加え、**圧力・組成・電気陰性度から毎回hL/hRを計算する輸送モード**を実装した。文献2000年の式(17)–(19)を使い、反応係数や輸送パラメータを文献密度に合わせて調整していない。

独立入力{len(unique)}条件中{len(successes)}条件で、宣言した縮約モデルの粒子・エネルギー・原子・圧力・電荷収支が整合した。10 mTorrの全条件が通過し、1 mTorrの6条件は両方式とも温度上限に達して失敗した。重複する基準入力を交差解析にも用いたためCSVは{len(records)}行ある。

10 mTorrの文献モデルO₂⁺密度との差は、100 Wで−13.24%から−2.52%、500 Wで+8.53%から+7.59%となった。複数電力に同じ輸送式を適用できるようになったが、低圧側の問題は解消していない。比較対象は**2001年論文のモデル計算値**であり、実験測定値ではない。参照値の不確かさは原文に示されておらず、この差を合否判定にしていない。

## 実装した式と適用範囲

出典：[Gudmundsson et al. (2000), On the plasma parameters of a planar inductive oxygen discharge](https://doi.org/10.1088/0022-3727/33/11/311)、印刷頁1329、式(17)–(19)。

```text
lambda_i = 1 / (nO*sigmaO + nO2*sigmaO2)
alpha = (nOminus + nO2minus + nO3minus) / ne
gamma = e*Te / (kB*Ti)
F = (1 + 3*alpha/gamma) / (1 + alpha)
hL = 0.86*F / sqrt(3 + L/(2*lambda_i))
hR = 0.8 *F / sqrt(4 + R/lambda_i)
```

hは正イオンのシース端／バルク密度比の近似値である。全粒子密度を更新するたびにλとαも更新し、同じhから壁粒子流束、シース電位、電子・イオン壁エネルギーを計算する。O⁺とO₂⁺には各々の質量のBohm速度を使う。中性粒子の壁生成物・流入排気・励起損失は前回と同じ扱いに保った。

原文のαはO⁻/neで、今回は全3負イオンへの拡張を明示した。基準値では全α={new['electronegativity']:.6f}、O⁻のみのα={transport['alpha_ominus']:.6f}。平均自由行程には基底OとO₂だけを使い、励起種・オゾンとの運動量交換は含めない。σO=σO₂=7.5×10⁻¹⁹ m²とし、分子の同一断面積は仮定である。Ti=Tg=600 Kを基準とし、Tiは解いた温度でも表面温度でもない。

原PDFを画像で確認した適用条件は `(R,L) ≥ lambda_i ≥ (Ti/Te)*(R,L)`。コードでは等号を含めてチェックし、R/λ、L/λ、γλ/R、γλ/Lも保存する。`transport_domain_valid`はこの大小条件とh≤1を確認する別指標で、物理的な妥当性の証明ではない。数値収支の成功・反応係数の温度域・輸送条件を別々に保持する。

**2001年式(8)–(16)の再現とは区別する。** 同式には電気陰性コアの境界とシース端の間の幅、および境界電子密度と体積平均正イオン密度の対応が必要で、現在の0D入力では閉じていない。任意のコア幅や密度変換を導入して2001年の完全再現と呼ぶことを避けた。原典整理は[文献確認メモ](../../docs/oxygen-transport-source-notes.md)に保存した。

## 同一条件での比較

純O₂、R=0.152 m、L=0.076 m、Tg=600 K、流量50 sccm。圧力・電力以外は前回の基準条件を使った。γO=0.17はSUS、γmeta=0.007はFe由来の値を参照しており、Si表面の係数ではない。吸収電力は電子・イオン壁損失を含む指定総電力で、40 MHz・250 Vpeakから計算した電力ではない。

| 圧力ごとの中性拡散係数 | O / O₂（m²/s） |
| --- | ---: |
| 1 mTorr | 8.8 / 6.3 |
| 10 mTorr | 1.2 / 0.84 |

文献の各圧力の参照出力を使い、電力間で固定した。新たなDの圧力外挿や組成依存則は導入していない。電子の運動量衝突頻度はO/O₂各10⁷ s⁻¹を基準とする仮定である。

| 10 mTorr・電力W | 方式 | Te eV | ne m⁻³ | O₂⁺ m⁻³ | 文献モデル値との差 |
| ---: | --- | ---: | ---: | ---: | ---: |
{chr(10).join(comparison_lines)}

1500 Wには同梱の数値比較点がないため差を示していない。1 mTorr・100/500/1500 Wは新旧の各3条件すべてでk20の適用上限4.5 eVに張り付き、収支も基準を満たさなかった。失敗候補の密度や損失を有効な物理結果として用いない。この失敗は、指定した近似・係数域・初期値群の下で整合解を取得できなかったことを意味し、放電が存在しないことを意味しない。

## 基準10 mTorr・500 Wの損失

| 損失項 | 固定h・W | 新輸送・W |
| --- | ---: | ---: |
{chr(10).join(loss_rows)}

新輸送のhL={transport['axial_edge_factor']:.6f}、hR={transport['radial_edge_factor']:.6f}、λ={1000*transport['ion_mean_free_path_m']:.4f} mm、有効イオン損失面積={transport['effective_area_m2']:.6f} m²。電子・イオン壁損失は80.3529 Wから87.6340 Wに増え、その分主に励起損失が変化した。総損失は{new['loss_w']['total']:.4f} Wである。

## 仮定に対する確認

10 mTorr・500 WでD倍率0.5/1/2、γO=0.15/0.17/0.19、γmeta=0.001/0.007/0.03の27交差条件を計算し、すべて収支と輸送の大小条件を通過した。ne={summary['cross']['ne_range_m3'][0]:.4e}–{summary['cross']['ne_range_m3'][1]:.4e} m⁻³、Te={summary['cross']['te_range_ev'][0]:.4f}–{summary['cross']['te_range_ev'][1]:.4f} eVとなった。

Tiを300/1200 K、σを基準の0.5/2倍、O/O₂各衝突頻度を0/10⁸ s⁻¹とする6追加条件も通過した。これらは仮定への感度確認で、Si表面や断面積の測定不確かさ分布ではない。全体として輸送閉包を追加しても、弾性衝突頻度の単純調整を主な較正手段にする根拠は得られていない。

## 検算と残る課題

別コードで全48反応イベント、壁輸送5項、粒子生成物、流入排気、エネルギー内訳、電子・イオン流束を再計算し、成功{len(successes)}条件で一致した。最大再計算差は壁係数3.6×10⁻¹⁶、反応率2.8×10⁻¹⁴、損失内訳1.4×10⁻¹⁴。

| 成功条件の最大相対残差 | 値 |
| --- | ---: |
| 全粒子 | {residuals['species_relative']:.2e} |
| エネルギー | {residuals['energy_relative']:.2e} |
| 酸素原子 | {residuals['oxygen_atoms_relative']:.2e} |
| 中性圧力 | {residuals['neutral_pressure_relative']:.2e} |
| 準中性 | {residuals['charge_relative']:.2e} |

関連テスト83件合格、今回のコード変更に関係しない既存実ソルバー2件を除外した。旧方式の基準結果は維持し、[前回スタディ](../oxygen-parameter-study/report.md)の118成功入力も変更後の独立検算で再確認した。各スタディのソースとSHA-256を保存し、今後の変更後にも比較できるようにした。

次の課題は2001年のコア／外側領域の空間閉包、低圧側の係数域と不足エネルギー、組成・EEDF由来の電子輸送、Si表面係数、CCPのRF吸収電力との連成である。今回は指定吸収電力の研究ソルバーへの改善で、ブラウザのO₂解析やDockerの通し確認は別途未完了である。

## 出力と再実行

- [比較図](01_paired_transport_comparison.png) / [SVG](01_paired_transport_comparison.svg)
- [損失・壁輸送の比較図](02_transport_power_budget.png) / [SVG](02_transport_power_budget.svg)
- [ケースCSV](cases.csv)、[全結果JSON](results.json)、[要約JSON](summary.json)、[独立検算](verification.json)、[入力計画](study-plan.json)

```bash
.venv/bin/python scripts/oxygen_transport_study.py
.venv/bin/python scripts/check_oxygen_study_results.py --input reports/oxygen-transport-study/results.json --output reports/oxygen-transport-study/verification.json
.venv/bin/python scripts/plot_oxygen_transport.py --input reports/oxygen-transport-study/cases.csv --output reports/oxygen-transport-study
.venv/bin/python scripts/summarize_oxygen_transport.py
```

実装：[壁輸送](../../backend/app/oxygen_transport.py)、[O₂定常収支](../../backend/app/oxygen_study.py)。粒子・縮約エネルギーの出典：[2001年論文](https://doi.org/10.1088/0022-3727/34/7/312)。
"""
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False))
    (args.output/"report.md").write_text(report)
    print(json.dumps({"output": str(args.output), "unique": len(unique), "converged": len(successes)}))


if __name__ == "__main__":
    main()

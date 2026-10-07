# O₂モデルの文献調査とCF₄の保留

調査日：2026-10-07。対象はDocker / PySpice / ngspiceによるCCP等価回路と定常0Dグローバルモデル。

## 採用方針

初期版の対象を単独Arと単独O₂に絞り、CF₄の採用は保留する。CF₄にも公開されたモデルがあり、今回の保留は開発・検証の優先順位による判断である。

O₂の化学反応はToneliらの2015年の体系を基礎候補とする。まずGudmundssonらの2001年のモデルで反応・収支計算の再現性を確認し、準安定分子を含む体系へ拡張する。化学反応系の再現と、今回のCCP装置条件の妥当性確認は分けて進める。

文献調査に続き、2001年モデルの反応表・出典注記と粒子計算用データを作成した。原文の保存則に疑義がある反応と不足する閉包が残るため、文献結果の数値再現は未完了である。実装・テストと物理バリデーションの現在の区別は[検証状況](validation-status.md)に記録する。

## 今回の装置条件

| 項目 | 条件 |
| --- | --- |
| ガス | 各ガス単独。初期版はAr / O₂、CF₄は保留 |
| RF | 40 MHz、駆動電極の正弦波ピーク250 V |
| 電極 | 300 mmウェハー側をRFカソード |
| 接地側 / 駆動側の有効面積 | 5 |
| 圧力 / 電極間隔 | 10 mTorr / 50 mm |
| ガス温度 | Tg = 300 K |
| 主な壁・電極材 | シリコン |
| 未確定 | 表面の酸化・被覆状態、表面温度Ts、表面反応係数、流量・滞留時間 |

シリコンという材質指定だけでは裸Si・酸化Si・付着膜付き表面を区別できない。Tg = 300 KからTsを決めず、材質、表面状態、温度、係数の出典をモデル入力として記録する。

## O₂の主要文献

| 文献 | 採用する役割と確認範囲 |
| --- | --- |
| J. T. Gudmundsson, I. G. Kouznetsov, K. K. Patel, M. A. Lieberman (2001), **Electronegativity of low-pressure high-density oxygen discharges**, J. Phys. D 34, 1100–1109. [DOI](https://doi.org/10.1088/0022-3727/34/7/312) / [本文PDF](https://cden.ucsd.edu/internal/Publications/Archive/SFR/Plasma/GudPatelKouzJPD01.pdf) | 11種の定常グローバルモデルの再現用。本文の反応表・壁反応表を確認。Maxwell分布を仮定する電子衝突係数の適用範囲はTe = 1–7 eV。今回のCCPの直接検証ではない。 |
| D. A. Toneli, R. S. Pessoa, M. Roberto, J. T. Gudmundsson (2015), **On the formation and annihilation of the singlet molecular metastables in an oxygen discharge**, J. Phys. D 48, 325202. [DOI](https://doi.org/10.1088/0022-3727/48/32/325202) | 準安定分子と負イオンを含む化学反応体系の基礎候補。書誌・要旨を確認し、詳細な反応資料は下記学位論文で確認。研究対象はICPであり、CCPの加熱モデルとして転用しない。 |
| 同著者 (2015), **A volume averaged global model study of the influence of the electron energy distribution and the wall material on an oxygen discharge**, J. Phys. D 48, 495203. [DOI](https://doi.org/10.1088/0022-3727/48/49/495203) | 電子エネルギー分布と壁材による結果の感度を検討する資料。書誌・要旨を確認。ステンレス・陽極酸化アルミの条件をSiの係数として使わない。 |
| J. T. Gudmundsson, D. I. Snorrason (2017), **On electron heating in a low pressure capacitively coupled oxygen discharge**, J. Appl. Phys. 122, 193302. [DOI](https://doi.org/10.1063/1.5003971) / [本文](https://arxiv.org/html/1711.09748) | 純O₂ CCPの加熱・密度・電気陰性度を比較する資料。本文を確認。10/50 mTorr、100–500 Vpeak、間隔45 mm。13.56 MHz・対称電極・ステンレスであり、40 MHz・面積比5・Siの検証は別途必要。 |

実装資料として、David Arruda Toneli (2016), **A volume averaged global model study of oxygen discharges – formation and annihilation of the singlet molecular metastables and effects of the electron energy distribution function**, ITA博士論文の[公開PDF](https://www.pgfis.ita.br/archive/604776c69e67c322257ff397)を確認した。Tables 4.1–4.5、Annex A、式5.2を、反応の分類・断面積・速度係数計算の参照元にできる。EEDFの形状を設定する方法と、Boltzmann方程式を解いてEEDFを求める方法は区別する。

回路連成の参考は、Yi Wang, Wan Dong, Yi-Fan Zhang, Liu-Qin Song, Yuan-Hong Song (2025), **Simulation of capacitively coupled Ar/O₂ discharges based on global/equivalent circuit model and an extended reaction set**, Chinese Physics B 34, 085201, [DOI・本文](https://cpb.iphy.ac.cn/article/doi/10.1088/1674-1056/add4e5)。本文で連成構成を確認した。Ar/O₂混合、13.56 MHz、150 Vpeak、間隔45 mmの比較条件であり、純O₂の今回条件を検証した文献ではない。

## シリコン表面と壁面反応

| 文献 | 確認できたことと適用限界 |
| --- | --- |
| K. Arts, S. Deijkers, R. L. Puurunen, W. M. M. Kessels, H. C. M. Knoops (2021), **Oxygen Recombination Probability Data for Plasma-Assisted Atomic Layer Deposition of SiO₂ and TiO₂**, J. Phys. Chem. C 125, 8244–8252. [DOI](https://doi.org/10.1021/acs.jpcc.1c01505) / [出版社版PDF](https://acris.aalto.fi/ws/portalfiles/portal/62675088/CHEM_Arts_et_al_Oxygen_Recombination_Data_2021_J_Phys_Chem_C.pdf) | 本文を確認。SiO₂のALD成長面での酸素原子再結合を測定。remote ICP、O₂/Ar、12–130 mTorr、Ts約100–240°C、イオンの入らない微細溝内の条件。裸Siやイオン照射されるCCP電極への標準係数として直接採用できない。 |
| A. S. Kovalev et al. (2005), **Kinetics of O₂(b¹Σg⁺) in oxygen RF discharges**, J. Phys. D 38, 2360–2370. [DOI](https://doi.org/10.1088/0022-3727/38/14/010) / [著者公開本文](https://www.researchgate.net/publication/231077457_Kinetics_of_O2b1g_in_oxygen_RF_discharges) | 本文を確認。石英上の準安定分子失活とRF放電・afterglowを比較する資料。Torr領域・石英であり、10 mTorr・Siの係数ではない。採用される表面係数には他文献からの引用値が含まれる。 |

今回確認した文献からは、指定条件のSi表面に適用できるO原子再結合・O₂(a¹Δg)失活・O₂(b¹Σg⁺)失活の係数を確定できなかった。酸化Siのデータがあっても、石英・ALD成長面・プラズマ照射酸化Siを同じ表面として扱わない。

モデルではこの3つの係数を別々に持ち、駆動電極・接地側・その他の壁について面積と表面条件を区別する。出典を指定した仮定値での感度解析を先に行い、係数の選択でne・Te・負イオン密度・自己バイアスがどの程度変わるか結果に残す。感度解析の範囲も仮定として明記し、実測値との区別を保つ。

## 初期O₂モデルの構築案

初期の縮約候補はe、O₂(X)、O(³P)、O₂⁺、O⁺、O⁻、O₂(a¹Δg)、O₂(b¹Σg⁺)、O(¹D)の9種とする。ただし、これは実装案であって、O₃系、O₂⁻、Herzberg状態等を省略できると検証済みの最小集合ではない。拡張反応系との比較で除外の影響を調べる。

取り込む反応は電離、解離、電子付着、電子・原子・準安定分子による脱離、電子イオン再結合、イオン間相互中和、準安定種の生成・失活、壁での再結合と中性粒子への戻りを含める。負イオンは体積内の反応損失とシースへの輸送を区別し、正イオンの壁流束は体積内の電気陰性度とシース端の値を区別して閉じる。

気相反応の係数は二体m³/s・三体m⁶/s、放射や一次損失はs⁻¹、壁反応確率は無次元として保存する。TeはeV、TgとTsはKを基本にする。壁反応確率を一次損失係数へ変換するときは、面積・体積・輸送モデルを明示する。表面確率をそのままs⁻¹として入力しない。

## 現在の試作から必要な拡張

反応インターフェースには一次・二体・三体反応、定数係数、壁生成物、符号付き電子エネルギー、宣言した元素数の保存確認を追加した。ただしToneli体系のデータ整備と物理モデルの閉包は未完了である。採用する反応に応じ、次の残作業を進める。

- 温度の種類、断面積とEEDFの出典を明示するデータ形式。
- 壁反応の生成物を含む酸素原子数の収支。必要に応じ流量・排気・中性粒子圧力の閉じ方。
- 多成分正イオンの壁流束と、電気陰性プラズマのシース端・Bohm条件の扱い。
- 超弾性衝突による電子エネルギーの増加を含む反応データと、全エネルギー収支の統合。
- EEDFと組成に対応する運動量衝突頻度、および電子加熱のモデル。

2017年の10 mTorr CCPではドリフト・両極性電場とシース運動による加熱が重要である。現在の一様なDrudeバルクの抵抗損失だけで、この加熱を十分に再現できると判断しない。反応表の追加だけをもって、O₂のne・Teが定量予測できるとは扱わない。

## 検証の順序

検証時は、圧力・周波数を参考文献に合わせて変更することがユーザーから許可されている。10 mTorr・40 MHzは目標装置の基準であり、文献再現試験をこの値に限定しない。

最初のCCP比較候補を次の2ケースとする。いずれも2017年論文に合わせ、13.56 MHz、電極間隔45 mm、対称電極、ステンレスとする。RF電圧は文献の100–500 Vpeakの範囲を使う。表面係数や中性粒子背景などのモデル仮定も合わせ、変更できない項目は差分として残す。

| ケース | 圧力 | 周波数 | 目的 | 実施状況 |
| --- | --- | --- | --- | --- |
| `o2-ccp-2017-10mtorr` | 10 mTorr | 13.56 MHz | 低圧での電子加熱と密度・電気陰性度の比較 | 未実施 |
| `o2-ccp-2017-50mtorr` | 50 mTorr | 13.56 MHz | 圧力による加熱・組成の変化の比較 | 未実施 |

比較量と許容誤差は結果を見る前に定める。体積平均と中心値、実効電子温度とモデルのTe、図からの読み取り誤差を区別し、傾向の一致と絶対値の一致を別に記録する。0Dモデルが持たない空間分布は直接の再現対象としない。係数を調整した場合は較正ケースとして記録し、別条件での検証と区別する。

1. 2001年モデルの公表条件で定常化学反応・粒子／エネルギー収支を再現する。電荷と酸素原子数の収支、係数の単位・適用範囲を確認する。
2. Toneli体系を使い、反応種の省略、EEDF、壁反応係数への感度を比較する。まず定常0Dに限定する。
3. 上記の2017年論文の2ケースを、文献条件でのCCP比較に使う。
4. 加熱・シース・表面の扱いを整えた上で、40 MHz・面積比5・Siの指定条件へ進める。この段階の定量的な妥当性には、対応する計測値や検証用計算との比較が必要である。

## CF₄の将来用参考文献

| 文献 | 用途・確認範囲 |
| --- | --- |
| D. A. Toneli, R. S. Pessoa, M. Roberto, J. T. Gudmundsson (2019), **A global model study of low pressure high density CF₄ discharge**, PSST 28, 025007. [DOI](https://doi.org/10.1088/1361-6595/aaf412) | 純CF₄の拡張化学反応モデル。出版者要旨・プレビュー・著者機関の書誌を確認。全反応係数表は未確認。 |
| T. Kimura, K. Ohe (2002), **Model and probe measurements of inductively coupled CF₄ discharges**, J. Appl. Phys. 92, 1780–1787. [DOI](https://doi.org/10.1063/1.1491023) | 本文の反応・壁損失・結果表を確認。純CF₄ ICP、13.56 MHz、2–30 mTorr。将来の化学反応系再現の候補。CCPの直接検証ではない。 |
| P. Ducluzaux, D. Ristoiu, G. Cunge, E. Despiau-Pujo (2024), **Impact of plasma operating conditions on the ion energy and angular distributions in dual-frequency capacitively coupled plasma reactors using CF₄ chemistry**, JVST A 42, 013002. [DOI](https://doi.org/10.1116/6.0003291) | 書誌・要旨を確認。13.56/40.68 MHzのDF-CCP、圧力比較30–200 mTorr。40 MHzに近い将来用資料だが、指定10 mTorrの直接検証ではない。本文反応表は未確認。 |

CF₄の反応データ抽出・標準モデルへの組み込みは今回の対象外とする。現試作に残るCF₄用の入力や固定ne/Te解析機能は、初期版の採用・検証対象を意味しない。

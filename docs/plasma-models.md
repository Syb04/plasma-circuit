# CCP・0D・分布回路モデルの仕様と適用範囲

RF回路の波形は実際のPySpice/ngspiceで計算し、粒子・エネルギーの外側収支とODEはSciPyで解く。固定波形や目標密度を計算結果として作らない。各機能が利用可能であること、数値解が収束すること、実験に対して妥当であることは別であり、ここに記載するプラズマモデルは実験未検証の縮約モデルである。

共通の実行入口は `app.simulation.execute_simulation(document, analysis)`。この入口が輸送・表面・加熱を各RF/化学反復へ接続し、RF診断、任意のIEDF、数値精細化を追加する。`app.plasma.execute_plasma` は基礎CCP・定常化学の入口であり、単独で呼ぶと共通入口の追加処理は付かない。

## 基準条件と設定

対象は単独Ar/O₂。CF₄は採用保留で、保存済み設定の読込互換性を維持する。CF₄の標準化学データは同梱しない。基準は40 MHz、理想電極駆動の主RF250 Vpeak、300 mm駆動電極、接地／駆動有効面積比5、10 mTorr = 1.333223684 Pa、間隔50 mm、300 K。

円板の駆動面積は0.0706858 m²、接地有効面積は0.353429 m²。基礎CCPの未指定体積は円板面積×間隔 = 0.00353429 m³、壁損失面積は駆動＋接地有効面積。O₂の化学・マクロモデルは円筒体積、両端面と側壁を使うため、RFの有効面積比と同じ形状ではない。仮定と実際の面積は結果へ保存する。

| 設定 | 既定値・意味 |
| --- | --- |
| `electron_density_m3` / `electron_temperature_ev` | 1e16 m⁻³ / 3 eV。固定CCP入力、または定常探索の初期値 |
| `momentum_collision_frequency_hz` | 1e7 s⁻¹。νの入力値であり2πを掛けない。検証済みガス別データではない |
| `ion_mass_amu` | Ar 39.948、O₂ 31.998。O₂ RF化学連成では壁流束で重み付けした有効O⁺/O₂⁺質量 |
| `electronegativity` | 0。負イオン総電荷密度／電子密度 |
| `wall_edge_factor` | 0.5。平均密度に対するシース入口密度比の入力仮定 |
| `plasma_volume_m3` / `wall_loss_area_m2` | 正の独立入力。基礎CCPでは上記幾何学を未指定値に使う |
| `cycles` / `points_per_cycle` | 80 / 256。許容範囲16〜120 / 64〜512の整数 |
| `max_global_iterations` | 18。許容範囲3〜40 |
| `secondary_electron_yield_cathode/anode` | 0。明示入力または各表面の収率を適用 |

CCPの固定状態範囲はTe=1〜20 eV、ne=1e10〜1e20 m⁻³。化学反応の係数範囲は別に制限する。保存済みCF₄の固定回路は69.006 amuを既定とする互換経路であり、CF₃⁺の仮定は標準化学モデルを意味しない。

## 電極電圧を与えるテンプレートと外部回路

空の専用設定テンプレートは `document.parameters.builtin_ccp_template=1` を使う。外部回路未設定時は主RFを電極側に与え、周期平均電極電流0になるDCバイアスを根探索する。単一周波数の基準電極電圧は `Vdc+250*sin(2*pi*40e6*t)`。面積比の電圧則は探索区間の初期推定にだけ使う。

同じ空のテンプレートで `settings.external_circuit` を指定すると、電圧源→出力抵抗→直列L→DCブロックC→PLASMA、および電極側並列Cを生成する。ここで `rf_peak_voltage` は**理想電源のピーク値**であり、電極振幅を固定しない。出力抵抗の未指定値は50 Ω、Lと各Cの0は素子省略を表す。`voltage_definition` は `source` のみ。

通常回路図では `PLASMA` を1個配置し、pを駆動、nを帰還として任意の対応素子を配線する。シースとバルクは同じngspice回路にスタンプされる。PLASMA部品設定と解析設定を統合し、同じキーでは解析設定を優先する。RF電圧源の波形は回路図側が優先する。複数の電圧源では `rf_source_id` で観測対象を明示する。専用テンプレートに部品・配線を足した場合や、通常図に `external_circuit` を重ねた場合はエラーにする。

DCから絶縁された駆動端子ではDCブロック容量を実回路内に保持し、初期容量電荷をshooting変数として周期平均電流0を求める。自己バイアスは得られた電極波形の平均値。明示的DC給電がある接続は平均電流0を強制しない。

RF電圧源波形 `kind=rf` は主周波数、任意の `second_frequency_hz`・`second_rf_peak_voltage`・`second_phase_deg`、任意のパルス包絡を扱う。全RF周波数と有効なパルス周波数は共通 `fundamental_frequency_hz` の整数倍でなければならない。自動推定は周波数比の分母を64までに制限する。RFの `pulse_off_fraction` はOFF時の**電圧振幅比**。パルス境界は矩形で、時間刻みの確認が必要。

`rf` 波形は8共通周期でsmoothstep立上げし、その後は入力振幅に一致する。通常図の `sin` 波形は明示した波形を保ち、減衰sinや非周期波形は周期CCP解析では拒否する。周期数は共通基本周期、点数／周期は最速の搬送周波数に対する刻みである。

## シースI/Qとバルクの方程式

基礎モデルはDrude直列R–Lと冷たい一様イオンのmatrix sheath。

```text
Lbulk = me * gap / (e² ne Acathode)
Rbulk = νm Lbulk
ni = ne * (1 + electronegativity)
uB = sqrt(e Te / mi)
Iion = e A ni wall_edge_factor uB
Ielectron = e A ne wall_edge_factor sqrt(e Te/(2π me)) exp(Vs/Te)
Vs = Vmetal - Vplasma
```

明示した `ion_wall_current_density_a_m2` がある場合はBohm式による回路イオン電流密度を置換する。O₂ RF連成では化学壁流束と電極等価回路の壁電流を一致させるために使う。

電子反発側で金属電荷は `Qmetal ≈ -A*sqrt(2 ε0 e ni_edge*(-Vs))`。シース崩壊付近はTeのoffsetとDebye容量で滑らかに接続し、順方向電子電流は熱流束の30倍へ滑らかに制限する。電子を引き付ける正の電子シースの物理は含まず、崩壊頻度を診断する。

シース導電電流は `Ielectron-Iion`。局所の二次電子収率γを指定すると、イオンに伴う conventional current は `-(1+γ)*Iion`。電荷ノードを `Q/1e-8`、補助容量を1e-8 Fとして実際の `I+dQ/dt` を端子へ戻す。可逆な容量電力を電子加熱へ入れない。

電流の正方向は金属からプラズマ。`Q(sheath_cathode/anode)` は金属電荷で電子反発時に負。対応するQ–V端子電圧は `V(metal_minus_plasma_cathode/anode)`。表示用の `V(sheath_cathode/anode)` は逆符号のプラズマ−金属降下であり、正の加速電圧を表す。

RF結果は最後の2共通周期の可変刻み全点で積分する。狭い電子電流パルスを一様間引きして平均しない。数値基準は電流・両シース電圧・電荷の周期間相対RMS差2%未満、RF電力予算残差1%未満。無DC給電時の平均電流は駆動イオン電流の1%未満、最小許容1 µA。刻み・周期数の精細化は別の確認として行う。

## 電子輸送・表面・加熱の任意拡張

共通入口の `electron_transport.mode` は既定 `explicit_nu`、任意 `cross_section_eedf`。後者は出典付き断面積から `k=∫σ(E)*sqrt(2eE/me)*f(E)dE`、`ν=Σn_target*k_target` を実積分する。EEDFはエネルギー確率密度（eV⁻¹）で、Maxwellianまたは正規化した表形式を使う。網羅するエネルギー範囲・尾確率・出典を確認し、断面積を外挿しない。Boltzmann/EEDF方程式を解く機能ではない。輸送EEDFを変えても化学のMaxwellian反応係数は自動置換されず、混成仮定を記録する。

`surface_parameters` を設定する場合は `cathode`・`anode`・`wall` の全3面に材料、状態、温度、`gamma_o`、`gamma_metastable`、`secondary_electron_yield` を指定する。出典または明示した仮定と、任意の範囲を保存する。材料名・表面温度から係数の値や温度則を推定しない。O₂は面積重み付けした `gamma/(2-gamma)` を既存の拡散＋表面抵抗閉包へ一度だけ使う。O₂(b)は組み込みO₂化学の動的種ではない。局所電極の二次電子加速電力は電子加熱へ一度だけ移し、導電シース電力との二重計上を避ける。

既定 `electron_heating.mode=bulk_drude` はバルクの `〈Rbulk*Ibulk²〉`。明示した `sheath_heating_resistance_ohm` の実抵抗電力と局所二次電子加速を使う場合は、それぞれを一度だけ加える。`moving_wall_maxwellian` は実シース波形から境界運動を求め、Maxwellian反射電子の仕事から可逆な圧力仕事を分離する。共通入口では推定した不可逆加熱を有効直列抵抗 `Rheat=Pestimate/〈Ibulk²〉` へ戻し、ngspice回路を反復する。推定値と実抵抗電力の固定点・拡張電力予算・適用範囲を診断する。単独の補助関数は波形上の推定だけを行う経路もある。

この逆作用は周期平均の有効抵抗による縮約閉包であり、位相依存運動論、電子枯渇、非局所輸送や二次電子雪崩を解かない。詳細な式、輸入形式、検算は[電子・表面モデル](electron-surface-models.md)を参照する。

## RF測定と電力の定義

`rf_diagnostics.measurement_planes` は電極側、外部回路の電源ポート側、任意のカスタム信号対を扱う。通常の外部プリセットでは電源ポートは50 Ω出力抵抗の負荷側。通常図では `rf_source_port={component_id,port}` で電圧源p、またはその直列出力抵抗の負荷側を指定できる。

保存波形の整数基本周期を可変刻みで積分する。フェーザはピーク規約 `x(t)=Re(X exp(jωt))`、電流は負荷へ入る方向。Zは大信号基本波 `V1/I1`、基本波電力は `Re(V1*conj(I1))/2`、総実電力は `〈V(t)I(t)〉`。RMS、DC電力、位相、電流THD、力率、高調波を別に返す。既定高調波数12、上限64で、保存刻みによる解像限界を報告する。

指定した正の実数Z₀の測定面のみ進行・反射電力を返す。DCを除いた全波形で `v±=(v±Z₀i)/2`、`P±=〈v±²〉/Z₀` とし、`Pforward-Preflected` と実電力を照合する。理想電源ピーク値をそのまま50 Ω負荷の吸収電力へ換算しない。

`electron_heating_w`、`ion_acceleration_power_w`、`conductive_sheath_power_w`、`secondary_electron_acceleration_power_w`、`electrode_absorbed_power_w` は異なる量。O₂とRFマクロの外部総吸収は

```text
Ptotal = Pelectron + Pconductive_sheath - Psecondary
Pelectron_retarding = Pion + Psecondary - Pconductive_sheath
```

で写像する。電子加熱とイオン加速の単純和を総吸収と同一視しない。電極端子電力との残差を保持し、収支を合わせるために電力を黙って再スケーリングしない。

## 純Arの定常グローバルモデル

モデル版 `ar-maxwellian-ground-state-0.1`。固定中性ガス、Maxwell電子分布、単一Ar⁺、`ne=ni`。

```text
kion(Te) = 2.34e-14 Te^0.59 exp(-17.44/Te) m³/s
kexc(Te) = 2.48e-14 Te^0.33 exp(-12.78/Te) m³/s
λwall = wall_edge_factor * uB * Aloss / Volume
particle balance: ne ng kion = ne λwall
Pcollision = ne ng Volume e (15.76 kion + 11.55 kexc)
Pelastic = 3 me/mi * νm * ne Volume e * max(Te-kB Tg/e,0)
Pwall = ne λwall Volume e (2.5+2γeffective) Te
energy balance: Pelectron = Pcollision + Pelastic + Pwall
```

2.5Teは電子の2Te＋Bohm入口0.5Te。二次電子使用時は熱的脱出の増分を含める。RFシースイオン加速をArの電子加熱として数えない。粒子収支でTeを求め、RF計算とエネルギー収支でneを探索する。Te探索範囲1〜7 eV、密度探索範囲1e12〜1e19 m⁻³に根がなければ収支不成立を返す。任意の輸送・表面・加熱設定は各RF反復で更新する。Ar定常モデルはRF連成経路であり、指定総電力による時間発展0Dとは別である。

係数はZhaoらの2024年論文 [Table 1, reactions 2–3](https://doi.org/10.7498/aps.73.20240952)の式・SI単位と照合した。同論文はLieberman・Lichtenberg, *Principles of Plasma Discharges and Materials Processing*, 2nd ed. (2005), pp.350–351を参照する。[書籍DOI](https://doi.org/10.1002/0471724254)の該当頁は直接閲覧していない。1〜7 eVは実装の探索制限であり文献保証範囲ではない。Ar*を動的に解かず、準安定種・段階電離や実測較正を含まない。

## O₂の縮約グローバルモデル

共通入口で `kind=global, gas=O2` を選ぶと、未指定 `chemistry_model` は `oxygen_reduced` となる。基礎 `execute_plasma` を直接呼ぶ場合はこの選択を明示する。明示した `reaction_model` があればユーザー反応表を優先する。

組み込みモデルは文献由来48粒子反応、16励起損失項、O₂/O₂(a)/O/O(¹D)/O₃、O⁻/O₂⁻/O₃⁻、O⁺/O₂⁺、電子を使う。純O₂供給、中性5種の圧力閉包、未知の共通中性排気率、荷電種の排気0、負イオン壁損失0、正イオン壁損失と準中性条件を使う。出典・訂正・省略反応は同梱データと結果metadataに保存する。

`power_mode=prescribed_absorbed` は正の `absorbed_power_w` を**電子・イオンを含む総プラズマ吸収電力**として指定する。RF波形・ネットリストは生成せず、定常密度と縮約収支の表を返す。RF電源電力やバルク電子加熱だけを指定する設定ではない。

既定 `power_mode=rf_coupled` はRF総吸収とイオン加速を別々に化学へ渡し、浮遊DCのイオン加速項を実RF値で置換する。O⁺/O₂⁺の壁イベント流束による有効質量と、円筒化学壁損失を2つの有効電極シースへ写像した電流密度をRFへ戻す。ne、Te、壁電流、電気陰性度、有効質量の対数差0.005未満とRF・化学各診断を連成収束の基準とする。円筒壁損失の等価シースへの写像は縮約仮定であり、多成分・電気陰性Bohmシースを解くものではない。

**エネルギー閉包は48反応すべての反応エネルギーではない。** 基底O₂/Oの電離、16励起項、弾性損失、電子壁脱出、Bohm入口、シースイオン加速を閉じる。励起標的電離k17/k19、電子脱離k7、超弾性k21、付着k3/k20、追加解離k22/k43の完全なエネルギー移送表、および重粒子脱離で生まれる電子の出生エネルギーは未解決。

k20の係数範囲は排他的 `1<Te<4.5 eV`。全体表の1〜7 eVだけで適用を判断しない。`transport_mode=explicit_h` はhL/hRを入力し、`gudmundsson_2000` は2000年の近似壁輸送を使う。反応係数範囲内であることと輸送近似の範囲内であることを別々に返す。2001年の空間core/edge閉包は実装していない。

既存候補 `gamma_o=0.17` はSUS上300 K、`gamma_meta=0.007` はFe上の値をSUSケースへ採用したもの。Si用係数ではない。Siで実行する場合は独立表面入力へ明示した仮定または確認した出典を指定する。[O₂文献調査](oxygen-literature-review.md)、[輸送出典](oxygen-transport-source-notes.md)、[既存のパラメータ研究](../reports/oxygen-parameter-study/report.md)を参照する。

## Ar/O₂の時間発展0Dとガス熱収支

`kind=global_transient` はBDFによる実ODE積分であり、定常解の補間で履歴を作らない。密度と電子エネルギー `We=1.5 ne e Te V`、Tgを状態として解く。電子密度は準中性条件から求め、変化するneを含めてTeを計算する。Arでは中性粒子の供給・排気・電離・壁からの帰還も解く。O₂は同じ48粒子反応と縮約エネルギーを使う。

既定 `power_mode=prescribed_absorbed` は総プラズマ電力を電子エネルギー方程式の入力として与え、電子・衝突・Bohm・浮遊シースイオン損失を引く。`rf_coupled` は `rf_update_interval_s` とパルス境界で実RF回路を再計算し、各区間の平均電子加熱・イオン加速・retarding work・総電力を保持する。RF逆作用や輸送はその再計算時に更新する。マクロのパルスは**平均電力比**を掛け、搬送波に再び同じ包絡を掛けない。RF OFF時の浮遊シースは保存電子エネルギーからの内部損失として閉じる。

通常回路図のRF連成では搬送周波数を明示した電源波形から読み取る。解析設定でマクロ包絡を明示しない場合、単一の電源包絡を引き継ぎ、OFF電圧振幅比を二乗して平均電力比へ変換する。これはOFF状態のRFを別に解かない二次電力近似であり、`rf_carrier_pulse_transform` に記録する。異なる電源包絡の混在は明示したマクロ包絡なしでは拒否する。

ガス熱収支は `Cgas*dTg/dt = Pelastic + f_inelastic*Pinelastic + f_ion*(Pion+PBohm) - Gwall*(Tg-Tbackground) - feed*Cp*(Tg-Tfeed)`。弾性移送は一度だけガスへ入る。任意の移送率と壁熱コンダクタンスはユーザー仮定で既定0。未指定熱容量は初期中性粒子数×理想気体Cv（Ar 3/2 kB、O₂ 5/2 kB）で一定。表面温度の発展・化学エンタルピーは解かない。初期圧力から粒子数を設定し、その後の圧力は中性密度とTgから求め、排気率は保持する。

Ar初期値は正のne/Teを指定する。O₂で初期粒子を省略すると、初期Tgで指定総電力の数値定常解を初期化に使う。明示する場合は全動的重粒子種の正密度と準中性条件を満たす。これは着火モデルではない。Te境界（Ar 1〜7 eV、O₂ 1〜4.5 eV）、ne床、負密度や不適切な浮遊シースに達するとイベントで止め、部分履歴と終了理由を返す。

終了時間は最大10 s、出力3〜5001点、パルス最大500周期。RF連成では設定する `rf_update_interval_s` が10共通搬送周期以上、再評価とパルスの合計区間は最大200。パルス境界や最後の端数で実区間が短くなる場合は時間尺度の分離を別に確認する。電子・ガス・全エネルギーの積分残差、原子収支、微小負密度補正、RF再評価履歴を保存する。ODE精細化に加え、より小さいRF再評価間隔の比較が必要。

## IEDFと径方向モデル

CCP/RF定常globalで `iedf.enabled=true` を指定すると、実際の駆動シース波形の共通基本周期で正イオン軌道を追跡し、入射エネルギーPDFと任意の電荷交換高速中性粒子PDFを返す。未指定イオンは平均密度に壁端係数を掛けた入口密度を使う。固定幅・一様電場・入力された一定断面積の近似であり、`e*Vs(t)` の単純ヒストグラムではない。粒子数・積分刻み・未到達率を確認する。IEDFの収束は独立して記録し、未解決粒子による未収束は親結果の `converged` にも反映する。マクロ0Dや指定電力のみの結果からRFシース波形を生成しない。

`kind=radial` は中心または外周給電の同心円環RF回路。各セルのDrude軸方向枝と平均シースの微分容量、ユーザー指定の電極シートR/Lを使い、ピークフェーザを解く。密度は一様な入力値であり、電圧・電力の分布を密度均一性の予測へ読み替えない。KCL・電力収支、Debye長、Drude skin depth、回路の径方向波長、振幅線形化などの適用範囲を返す。完全な電磁界・反応拡散・PICではない。詳細は[IEDF・径方向モデル](ion-radial-models.md)。

## ユーザー反応モデル

`settings.reaction_model` と `initial_species_densities_m3` で出典付き定常反応表を登録する。O₂/CF₄では負イオン・解離中性種を必要とし、ガス名だけから反応係数を作らない。粒子専用 `energy_mode=particle_only...` の表をグローバル電子エネルギー閉包として使うことは拒否する。

一次・二体・三体のSI定数またはTe依存表、準中性 `ne=Σ zs ns`、符号付き電子エネルギー損失、壁生成物と一次壁損失を扱う。全動的種の正初期密度を指定する。電荷数は-1/0/1、気相反応の化学量論は整数で、気相反応の電荷保存を確認する。壁生成物は平均生成数の小数を許す。`elements` を宣言した表では全種に組成を指定して元素保存を確認し、壁の電荷変化は準中性電子閉包で扱う。

係数が正なら対数補間、0を含むなら線形補間し、表範囲外へ外挿しない。正イオンの既定壁モデルはBohm、明示定数は `wall_loss_model=constant, wall_loss_s=...`。負イオン・中性種は明示した定数壁損失を使う。負イオン壁流束が正イオンを超える電子注入境界は拒否する。多イオンは密度重み有効質量で単一シースへ渡し、電気陰性Bohm補正や多成分シースは含まない。

モデル・反応表・出典・入力・ネットリスト・収支を保存する。登録表は `user_supplied_unvalidated` とし、正の解や小さい残差から物理的妥当性を宣言しない。操作例と精細化・参照比較・パッケージは[解析ワークフロー](analysis-workflows.md)、検証証跡は[検証状況](validation-status.md)を参照する。

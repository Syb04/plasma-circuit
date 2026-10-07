# O₂イオン壁輸送：原典確認メモ

確認日：2026-10-07。係数へのフィットは行わない。2000年式と2001年式を別モデルとして扱う。

## 2000年の実装可能な近似

出典：[Gudmundsson et al., *On the plasma parameters of a planar inductive oxygen discharge*](https://cden.ucsd.edu/internal/Publications/Archive/SFR/Plasma/PlanarIndOxyDisch.pdf)、p.1329、式(17)–(19)。

\[
F={1+3\alpha/\gamma\over1+\alpha},\qquad
h_L={0.86F\over\sqrt{3+L/(2\lambda_i)}},\qquad
h_R={0.8F\over\sqrt{4+R/\lambda_i}}.
\]

\[
\alpha={n_{O^-}\over n_e},\qquad \gamma={T_e\over T_i},\qquad
\lambda_i^{-1}=\sum_j n_{g,j}\sigma_{i,j}.
\]

原典の (h) はシース端／bulk正イオン密度比。衝突断面積候補は (7.5\times10^{-19}\,m^2\)。温度比には同じエネルギー単位を使う：入力 (T_i[K]) なら γ=eT_e/(k_BT_i)。(T_i=T_g) は明示する仮定である。

実装契約：`gudmundsson_2000` を固定h入力とは別モードにする。各反復のground O/O₂密度から共通 λ を計算する。全負イオンを使う α=Σn₋/ne は原典O⁻近似の拡張と記録する。粒子・壁エネルギー収支に同じhを使う。

原PDFの**印刷頁1329**を画像で確認した低圧拡散条件は \((R,L)\geqslant\lambda_i\geqslant(T_i/T_e)(R,L)\)。記号はgreater-than-or-equal（斜めの等号）で、≫ではない。したがって原文通りの順序診断は λ≤min(R,L) と λ≥max(R,L)/γ。R/λ、L/λ、γλ/R、γλ/Lも保存し、この順序判定だけを検証合格と呼ばない。

同論文p.1331は、電気陽性の平行平板／無限長円筒式を修正した近似であり、酸素のcore/edge遷移への一般化と影響調査が必要としている。これは2001年の輸送再現でもSi装置検証でもない。

## 2001年の式と不足する入力

出典：[Gudmundsson et al., *Electronegativity of low-pressure high-density oxygen discharges*](https://cden.ucsd.edu/internal/Publications/Archive/SFR/Plasma/GudPatelKouzJPD01.pdf)、pp.1103–1104、式(8)–(16)。

\[
\Delta=\ell_p-\ell_-,\qquad
h_L={n_{s+}\over n_{e0}}=
\left({1+\chi_0^{3/2}\over1+\chi_s^{3/2}}\right)^{1/3}.\tag{8}
\]
\[
\chi_s=\beta^{-2/3}
{\sum_j c_j\pi\Delta/(2\lambda_{i,j})\over\sum_jc_j},\tag{9}
\]
\[
c_j=\nu_{iz,j}\left({\pi m_{i,j}\over2e\lambda_{i,j}}\right)^{1/2},\tag{10}
\]
\[
\beta=\sum_j\left({\pi\Delta\over2\lambda_{i,j}}\right)^{1/2}
{\nu_{iz,j}\Delta\over u_{B0,j}},\quad
u_{B0,j}=\sqrt{eT_e/m_{i,j}},\quad \nu_{iz,j}=n_{g,j}k_{iz,j},\tag{11}
\]
\[
\lambda_{i,j}=(n_{g,j}\sigma_{i,j})^{-1},\qquad
\chi_0=\beta^{-2/3}{\pi\Delta\over2}
\left\langle{w_j^2\over\lambda_{i,j}}\right\rangle,\tag{12--13}
\]
\[
\left\langle{w_j^2\over\lambda_{i,j}}\right\rangle=
{\sum_j n_{+,j}(u_{in,j}/u_{B,j})^2/\lambda_{i,j}\over\sum_jn_{+,j}},\tag{14}
\]
\[
u_{in,j}={2\bar D_{a,j}\alpha_0\over L},\qquad
\bar\alpha={\sum_i n_{-,i}\over n_e},\qquad \alpha_0={3\over2}\bar\alpha,\tag{15}
\]
\[
\bar D_{a,j}\simeq D_j{1+\gamma+2\gamma\bar\alpha\over1+\gamma\bar\alpha},\qquad
D_j={eT_j\over m_j\nu_j}=v_{Tj}\lambda_{i,j},\qquad
v_{Tj}=\sqrt{eT_j/m_j}.\tag{16}
\]

ν_jはイオン運動量衝突頻度で、電子のν_mとは異なる。単一イオン近似のw²/λを多イオン密度重みで置き換える。原典は弱い電気陰性coreと電気陽性edgeを仮定し、短円筒L<2RについてhR=hLを採用する。

現0D入力だけではΔの閉包、境界電子密度ne0とbulk正イオン密度の対応、種別生成頻度の採択が不足する。式(8)のhをbulkイオン壁式へ直接代入したり、Δ=L/2やh/(1+α)を原典確定値として追加しない。2001年モードはこの不足を記録し、完全実装と表示しない。

## 比較の契約

同じ化学、流量、中性圧力、縮約エネルギー、表面係数で固定h走査と比較する。h、λ、αの定義、Ti、断面積、領域診断を結果に残す。1mTorrの領域外結果も省略せず区別する。密度の文献差は説明的な比較であり、最小差の輸送式を採用するフィット基準にしない。

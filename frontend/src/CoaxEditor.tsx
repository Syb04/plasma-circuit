import {useEffect, useState} from 'react';
import type {Component, Json} from './types';
import {api, ApiError} from './types';
import {ScientificInput} from './ScientificInput';
import {JsonEditor} from './Editors';

export const coaxDefaults = {
  inner_diameter_m: 1e-3, shield_inner_diameter_m: 3.35e-3, length_m: 1,
  relative_permittivity: 2.1, relative_permeability: 1, loss_tangent: 2e-4,
  inner_resistivity_ohm_m: 1.724e-8, shield_resistivity_ohm_m: 1.724e-8,
  shield_thickness_m: .15e-3, reference_frequency_hz: 40e6, segments: 32,
};
export const coaxFields = [
  {key: 'inner_diameter_m', label: '内部導体直径（mm）', scale: 1e-3, min: 0, exclusive: true},
  {key: 'shield_inner_diameter_m', label: 'シールド内径（mm）', scale: 1e-3, min: 0, exclusive: true},
  {key: 'length_m', label: 'ケーブル長さ（m）', scale: 1, min: 0, exclusive: true},
  {key: 'relative_permittivity', label: '誘電体の比誘電率 εr', scale: 1, min: 0, exclusive: true},
  {key: 'relative_permeability', label: '誘電体の比透磁率 μr', scale: 1, min: 0, exclusive: true},
  {key: 'loss_tangent', label: '誘電正接 tan δ', scale: 1, min: 0, max: 1, maxExclusive: true},
  {key: 'inner_resistivity_ohm_m', label: '内部導体抵抗率（Ω·m）', scale: 1, min: 0},
  {key: 'shield_resistivity_ohm_m', label: 'シールド抵抗率（Ω·m）', scale: 1, min: 0},
  {key: 'shield_thickness_m', label: 'シールド厚さ（mm）', scale: 1e-3, min: 0, exclusive: true},
  {key: 'reference_frequency_hz', label: '損失の基準周波数（MHz）', scale: 1e6, min: 0, exclusive: true},
  {key: 'segments', label: '線路の分割数', scale: 1, min: 1, max: 256, integer: true},
] as const;
type Preview = {nominal_impedance_ohm: number; tem_delay_s: number; capacitance_f_m: number;
  inductance_h_m: number; attenuation_db: number; attenuation_db_m: number;
  dc_resistance_ohm_m: number; rf_resistance_ohm_m: number; warnings: string[]};

export function CoaxEditor({component, onChange}: {component: Component; onChange: (next: Component) => void}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const parameters = {...coaxDefaults, ...component.parameters};
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    const timer = setTimeout(() => {
      void api<Preview>('/coax/preview', {method: 'POST', signal: controller.signal, body: JSON.stringify({parameters: component.parameters})})
        .then(value => {if (!controller.signal.aborted) {setPreview(value); setLoading(false);}})
        .catch(reason => {if (!controller.signal.aborted) {setPreview(null); setLoading(false); setError(reason instanceof ApiError ? reason.message : '線路定数を取得できません。サーバー接続を確認してください。');}});
    }, 250);
    return () => {clearTimeout(timer); controller.abort();};
  }, [component.id, component.parameters]);

  function inputs(list: readonly typeof coaxFields[number][]) {
    return <div className="form-grid">{list.map(field => {
      const key = field.key;
      const value = typeof parameters[key] === 'number' ? Number(parameters[key]) : undefined;
      const inner = Number(parameters.inner_diameter_m) / 1e-3;
      const shield = Number(parameters.shield_inner_diameter_m) / 1e-3;
      const min = key === 'shield_inner_diameter_m' ? inner : field.min;
      const max = key === 'inner_diameter_m' ? shield : 'max' in field ? field.max : undefined;
      return <label key={key}>{field.label}<ScientificInput label={field.label} value={value} scale={field.scale}
        min={min} max={max} minExclusive={key === 'shield_inner_diameter_m' || ('exclusive' in field && field.exclusive)}
        maxExclusive={key === 'inner_diameter_m' || ('maxExclusive' in field && field.maxExclusive)}
        integer={'integer' in field && field.integer} required
        onChange={next => onChange({...component, parameters: {...component.parameters, [key]: next}})}/></label>;
    })}</div>;
  }
  return <div className="coax-editor">
    <div className="info-box">p1・n1が入力、p2・n2が出力です。pは内部導体、nは共通シールド基準。シールド径には誘電体に接する内径を指定してください。</div>
    <h4>寸法・誘電体</h4>{inputs(coaxFields.slice(0, 6))}
    <h4>導体・解析条件</h4>{inputs(coaxFields.slice(6))}
    <p className="muted text-small">指数表記も使えます。導体は非磁性の中実円柱・平滑なシールドを想定します。抵抗率0で理想導体、誘電正接0で誘電体損失なし。</p>
    <section className="coax-derived" aria-label="同軸ケーブルの線路定数" aria-busy={loading}>
      <h4>線路定数</h4>
      {loading ? <p role="status">計算中…</p> : error ? <p className="field-error" role="alert">{error}</p> : preview && <>
        <dl>{[
          ['TEM特性インピーダンス', preview.nominal_impedance_ohm, 'Ω'],
          ['TEM伝搬遅延', preview.tem_delay_s * 1e9, 'ns'],
          ['分布容量', preview.capacitance_f_m * 1e12, 'pF/m'],
          ['外部インダクタンス', preview.inductance_h_m * 1e9, 'nH/m'],
          ['DCループ抵抗', preview.dc_resistance_ohm_m, 'Ω/m'],
          ['基準周波数の導体抵抗', preview.rf_resistance_ohm_m, 'Ω/m'],
          ['基準周波数の減衰', preview.attenuation_db_m, 'dB/m'],
          ['ケーブル全長の減衰', preview.attenuation_db, 'dB'],
        ].map(([name, value, unit]) => <div key={String(name)}><dt>{name}</dt><dd>{Number(value).toPrecision(5)} {unit}</dd></div>)}</dl>
        {preview.warnings.map(warning => <p className="warning-box" key={warning}>{warning}</p>)}
      </>}
    </section>
    <p className="muted text-small">表皮効果と誘電体損失を基準周波数で合わせた受動RLC近似です。広帯域・高調波・急峻なパルスでは、基準周波数と分割数を変えて確認してください。</p>
    <details className="advanced"><summary>同軸ケーブルの全パラメータ</summary><JsonEditor value={component.parameters} onApply={value => {
      if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('JSONオブジェクトを指定してください。');
      onChange({...component, parameters: value as Record<string, Json>});
    }}/></details>
  </div>;
}

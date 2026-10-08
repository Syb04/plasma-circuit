import type {Component, Json} from './types';
import {ScientificInput} from './ScientificInput';

const fields = [
  {key: 'IS', label: '飽和電流 IS（A）', min: 0, minExclusive: true},
  {key: 'N', label: '理想係数 N', min: 0, minExclusive: true},
  {key: 'RS', label: '直列抵抗 RS（Ω）', min: 0},
  {key: 'BV', label: '逆ブレーク電圧 BV（V）', min: 0, minExclusive: true},
  {key: 'IBV', label: 'ブレーク時電流 IBV（A）', min: 0, minExclusive: true},
  {key: 'CJO', label: 'ゼロバイアス接合容量 CJO（F）', min: 0},
  {key: 'VJ', label: '接合電位 VJ（V）', min: 0, minExclusive: true},
  {key: 'M', label: '接合傾斜係数 M', min: 0},
  {key: 'TT', label: '走行時間 TT（s）', min: 0},
  {key: 'EG', label: 'エネルギーギャップ EG（eV）', min: 0, minExclusive: true},
  {key: 'XTI', label: '飽和電流の温度指数 XTI'},
  {key: 'FC', label: '順方向接合容量係数 FC', min: 0, max: 1, maxExclusive: true},
  {key: 'TNOM', label: 'モデル基準温度 TNOM（°C）', min: -273.15, minExclusive: true},
];

export function DiodeEditor({component, onChange}: {
  component: Component; onChange: (component: Component) => void;
}) {
  const original = component.parameters.model_parameters;
  const parameters = original && typeof original === 'object' && !Array.isArray(original) ? original : {};
  function update(key: string, value?: number) {
    const next: Record<string, Json> = {...parameters};
    if (value === undefined) delete next[key]; else next[key] = value;
    onChange({...component, parameters: {...component.parameters, model_parameters: next}});
  }
  const inputs = (list: typeof fields) => <div className="form-grid">{list.map(field =>
    <label key={field.key}>{field.label}<ScientificInput label={field.label}
      value={typeof parameters[field.key] === 'number' ? Number(parameters[field.key]) : undefined}
      min={field.min} max={'max' in field ? field.max : undefined}
      minExclusive={'minExclusive' in field && field.minExclusive} maxExclusive={'maxExclusive' in field && field.maxExclusive} required={false}
      placeholder={field.key === 'BV' ? '未指定（逆降伏なし）' : 'ngspice既定値'}
      onChange={value => update(field.key, value)} onClear={() => update(field.key)}/></label>
  )}</div>;
  return <div className="diode-editor">
    <h4>ダイオードモデル</h4>
    <p className="muted text-small">各部品に専用モデルを生成します。Nは理想係数（放射係数）です。指数表記も使えます。空欄の項目はngspice既定値を使用します。</p>
    {inputs(fields.slice(0, 5))}
    <details className="advanced"><summary>接合容量・走行時間・温度依存</summary>{inputs(fields.slice(5))}</details>
  </div>;
}

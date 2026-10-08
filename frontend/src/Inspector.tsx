import { useEffect, useRef, useState } from 'react';
import { Copy, Plus, SlidersHorizontal, FileCode2, Check, X } from 'lucide-react';
import type { Analysis, AnalysisKind, CircuitDocument, Component, Json } from './types';
import { analysisNames, clone, defaultSettings, isPlasmaAnalysis, isPrescribedPower } from './types';
import { PlasmaControls } from './PlasmaControls';
import {TimeInput, type TimeUnit} from './TimeInput';
import {ScientificInput} from './ScientificInput';
import {DiodeEditor} from './DiodeEditor';
import {CoaxEditor, isCoax} from './CoaxEditor';
import {NodeLabelEditor} from './NodeLabels';
import type {Endpoint} from './types';

import {JsonEditor} from './Editors';
export {JsonEditor} from './Editors';
const fieldNames: Record<string,string>={value:'値（SI）',dc:'DC値',ac_magnitude:'AC振幅',ac_phase:'AC位相（°）',model:'モデル名',area:'面積倍率',gain:'利得',control_source:'制御電圧源ID',inductor1:'インダクタ1 ID',inductor2:'インダクタ2 ID',coupling:'結合係数'};
function ParameterInput({value,onChange,label}:{value:Json;onChange:(value:Json)=>void;label:string}){
  const [text,setText]=useState(String(value??''));const emitted=useRef<Json>(value);
  useEffect(()=>{if(value!==emitted.current){setText(String(value??''));emitted.current=value;}},[value]);
  if(typeof value==='boolean')return <select aria-label={label} value={String(value)} onChange={e=>onChange(e.target.value==='true')}><option value="true">true</option><option value="false">false</option></select>;
  if(typeof value==='number')return <ScientificInput label={label} value={value} onChange={onChange}/>;
  return <input aria-label={label} value={text} spellCheck={false} onChange={e=>{const next=e.target.value;setText(next);emitted.current=next;onChange(next);}}/>;
}
function EddEditor({component,onChange}:{component:Component;onChange:(component:Component)=>void}) {
  type Branch = {positive:string;negative:string;current:string;charge:string};
  const original=component.parameters.branches;
  const [branches,setBranches]=useState<Branch[]>(Array.isArray(original)?original as unknown as Branch[]:[]);
  useEffect(()=>setBranches(Array.isArray(component.parameters.branches)?clone(component.parameters.branches) as unknown as Branch[]:[]),[component]);
  const [error,setError]=useState('');
  function update(index:number,key:keyof Branch,value:string){setBranches(branches.map((b,i)=>i===index?{...b,[key]:value}:b));}
  function apply() {
    const normalized=branches.map(b=>({...b,positive:b.positive.trim(),negative:b.negative.trim()}));
    const ports=[...new Set(normalized.flatMap(b=>[b.positive,b.negative]))];
    if (!normalized.length||ports.some(p=>!p)||normalized.some(b=>b.positive===b.negative)) {setError('各枝に異なる＋端子・−端子を指定してください。複数の枝で同じ端子を共有できます。');return;}
    setError('');onChange({...component,ports,parameters:{...component.parameters,branches:normalized as unknown as Json}});
  }
  return <div className="edd-editor"><div className="info-box">枝 k の総電流 = Iₖ + dQₖ/dt<br/>Vₖ は＋端子から−端子への電圧。Iₖ は伝導電流です。</div>
    {branches.map((branch,i)=><div className="edd-branch" key={i}><div className="branch-heading"><strong>枝 {i+1}</strong><button aria-label={`枝${i+1}を削除`} className="icon-button" disabled={branches.length===1} onClick={()=>setBranches(branches.filter((_,j)=>j!==i))}><X size={14}/></button></div><div className="form-grid"><label>＋端子<input value={branch.positive} onChange={e=>update(i,'positive',e.target.value)}/></label><label>−端子<input value={branch.negative} onChange={e=>update(i,'negative',e.target.value)}/></label></div><label>I{i+1}（A）<input className="code-input" value={branch.current} onChange={e=>update(i,'current',e.target.value)} spellCheck={false}/></label><label>Q{i+1}（C）<input className="code-input" value={branch.charge} onChange={e=>update(i,'charge',e.target.value)} spellCheck={false}/></label></div>)}
    <div className="flex-row"><button className="button small secondary" onClick={()=>setBranches([...branches,{positive:`p${branches.length+1}`,negative:`n${branches.length+1}`,current:'0',charge:'0'}])}><Plus size={13}/>枝を追加</button><button className="button small primary" onClick={apply}>枝を適用</button></div>{error&&<p className="field-error">{error}</p>}
    <JsonEditor label="定数パラメータ" value={component.parameters.parameters??{}} rows={4} onApply={v=>{if(!v||Array.isArray(v)||typeof v!=='object')throw new Error('JSONオブジェクトを指定してください。');onChange({...component,parameters:{...component.parameters,parameters:v as Json}});}}/>
    <JsonEditor label="中間式" value={component.parameters.intermediates??{}} rows={4} onApply={v=>{if(!v||Array.isArray(v)||typeof v!=='object')throw new Error('JSONオブジェクトを指定してください。');onChange({...component,parameters:{...component.parameters,intermediates:v as Json}});}}/>
    <p className="muted text-small">他の枝の V1, V2…、I1, I2…を参照できます。式はサポートされた数学構文で評価します。</p>
  </div>;
}
function SourceWaveform({component,onChange}:{component:Component;onChange:(component:Component)=>void}){
  const original=component.parameters.waveform;
  const waveform=original&&typeof original==='object'&&!Array.isArray(original)?original:{};
  const kind=String(waveform.kind??'dc');
  function update(key:string,value?:number){const next={...waveform};if(value===undefined)delete next[key];else next[key]=value;onChange({...component,parameters:{...component.parameters,waveform:next}});}
  const definitions:Record<string,Record<string,Json>>={dc:{},sin:{kind:'sin',offset:0,amplitude:250,frequency:40e6},rf:{kind:'rf',frequency_hz:40e6,rf_peak_voltage:250,second_rf_peak_voltage:0,second_phase_deg:0},pulse:{kind:'pulse',initial:0,pulsed:1,delay:0,rise:1e-9,fall:1e-9,width:1e-6,period:2e-6}};
  const sourceUnit=component.kind==='I'?'A':'V';
  const labels:Record<string,string>={offset:`オフセット（${sourceUnit}）`,amplitude:`ピーク振幅（${sourceUnit}）`,frequency:'周波数（Hz）',frequency_hz:'RF周波数（Hz）',rf_peak_voltage:'RFピーク電圧（V）',second_frequency_hz:'第2 RF周波数（Hz）',second_rf_peak_voltage:'第2 RFピーク電圧（V）',second_phase_deg:'第2 RF位相（°）',pulse_frequency_hz:'包絡パルス周波数（Hz）',pulse_duty_cycle:'包絡デューティ（0〜1）',pulse_off_fraction:'OFF時振幅比（0〜1）',initial:'初期値',pulsed:'ON値',delay:'遅延（s）',rise:'立上り（s）',fall:'立下り（s）',width:'ON幅（s）',period:'周期（s）'};
  const optional=(key:string)=>kind==='rf'&&!['frequency_hz','rf_peak_voltage'].includes(key);
  return <details className="advanced"><summary>電源波形・2周波数RF</summary><label>波形<select aria-label="波形" value={kind} onChange={e=>{const next=definitions[e.target.value];const parameters={...component.parameters};if(e.target.value==='dc')delete parameters.waveform;else parameters.waveform=next;onChange({...component,parameters});}}><option value="dc">DC</option><option value="sin">正弦波</option>{component.kind==='V'&&<option value="rf">RF・2周波数・パルス包絡</option>}<option value="pulse">パルス</option>{kind==='pwl'&&<option value="pwl">PWL（詳細JSON）</option>}</select></label>{kind!=='dc'&&kind!=='pwl'&&<><p className="muted text-small">指数表記で入力できます（例: 4e7 Hz、1e-9 s）。</p>{kind==='rf'&&<p className="muted text-small">電圧は電源のピーク値です。第2振幅0で単一RF。パルス周波数は未指定で連続波になります。</p>}<div className="form-grid">{(kind==='rf'?['frequency_hz','rf_peak_voltage','second_frequency_hz','second_rf_peak_voltage','second_phase_deg','pulse_frequency_hz','pulse_duty_cycle','pulse_off_fraction']:Object.keys(definitions[kind]??{}).filter(k=>k!=='kind')).map(key=><label key={`${kind}-${key}`}>{labels[key]??key}<ScientificInput label={labels[key]??key} value={typeof waveform[key]==='number'?waveform[key]:undefined} required={!optional(key)&&waveform[key]!==undefined} onClear={optional(key)?()=>update(key):undefined} onChange={value=>update(key,value)}/></label>)}</div></>}</details>;
}
export function ComponentInspector({component,components,document,selectedPort,onSelectPort,onDocumentChange,onChange,onDuplicate,onDelete}:{component:Component;components:Component[];document:CircuitDocument;selectedPort:string|null;onSelectPort:(endpoint:Endpoint)=>void;onDocumentChange:(doc:CircuitDocument)=>void;onChange:(component:Component)=>void;onDuplicate:()=>void;onDelete:()=>void}) {
  const references=(key:string)=>components.filter(c=>c.id!==component.id&&(key==='control_source'?c.kind==='V':c.kind==='L'));
  return <div className="inspector-section component-inspector"><div className="panel-heading"><SlidersHorizontal size={16}/><h3>部品の設定</h3><span className="kind-tag">{component.kind}</span></div><label>部品名<input value={component.label} onChange={e=>onChange({...component,label:e.target.value})}/></label>
    <NodeLabelEditor document={document} component={component} selectedPort={selectedPort} onSelectPort={onSelectPort} onChange={onDocumentChange}/>
    {isCoax(component.kind)?<CoaxEditor component={component} onChange={onChange}/>:component.kind==='PLASMA'?<><div className="info-box">2端子CCPの非線形シース・バルク回路。電源・RLCに接続して通常の回路解析、CCP・グローバル解析で駆動できます。</div><label>ガス<select aria-label="ガス" value={String(component.parameters.gas??'Ar')} onChange={e=>onChange({...component,parameters:{...component.parameters,gas:e.target.value}})}><option value="Ar">Ar</option><option value="O2">O₂</option></select></label><div className="form-grid">{plasmaFields.filter(f=>!['frequency_hz','rf_peak_voltage'].includes(f.key)).map(field=><NumberField key={field.key} field={field} value={component.parameters[field.key]??defaultSettings.ccp[field.key]} onChange={v=>onChange({...component,parameters:{...component.parameters,[field.key]:v}})}/>)}</div><PlasmaControls kind="ccp" componentMode values={component.parameters} set={(key,value)=>onChange({...component,parameters:{...component.parameters,[key]:value}})}/><details className="advanced"><summary>PLASMA全パラメータ</summary><JsonEditor value={component.parameters} onApply={v=>{if(!v||typeof v!=='object'||Array.isArray(v))throw new Error('JSONオブジェクトを指定してください。');onChange({...component,parameters:v as Record<string,Json>});}}/></details></>:component.kind==='EDD'?<EddEditor component={component} onChange={onChange}/>:<>
      {Object.entries(component.parameters).filter(([key,v])=>typeof v!=='object'&&!(component.kind==='D'&&'model_parameters' in component.parameters&&key==='model')).map(([key,value])=><label key={key}>{fieldNames[key]??key}{['control_source','inductor1','inductor2'].includes(key)?<select value={String(value??'')} onChange={e=>onChange({...component,parameters:{...component.parameters,[key]:e.target.value}})}><option value="">参照する部品を選択</option>{value&&!references(key).some(c=>c.id===value)&&<option value={String(value)}>未解決: {String(value)}</option>}{references(key).map(c=><option key={c.id} value={c.id}>{c.label} ({c.id})</option>)}</select>:<ParameterInput label={fieldNames[key]??key} value={value} onChange={next=>onChange({...component,parameters:{...component.parameters,[key]:next}})}/>}</label>)}
      {component.kind==='D'&&'model_parameters' in component.parameters&&<DiodeEditor component={component} onChange={onChange}/>}
      {['V','I'].includes(component.kind)&&<SourceWaveform component={component} onChange={onChange}/>}
      <details className="advanced"><summary>全パラメータ・波形設定</summary><JsonEditor value={component.parameters} onApply={v=>{if(!v||Array.isArray(v)||typeof v!=='object')throw new Error('JSONオブジェクトを指定してください。');onChange({...component,parameters:v as Record<string,Json>});}} label="parameters"/></details>
    </>}
    {(component.kind==='X'||component.kind==='B')&&<label>端子名（カンマ区切り）<input value={component.ports.join(',')} onChange={e=>{const ports=e.target.value.split(',').map(s=>s.trim()).filter(Boolean);if(ports.length&&new Set(ports).size===ports.length)onChange({...component,ports});}}/></label>}
    <div className="component-footer"><span className="muted">ID: {component.id}</span><span className="muted">端子: {component.ports.join(' · ')||'なし'}</span><button className="text-button" title="複製 Ctrl+D / ⌘D" disabled={components.length>=500} onClick={onDuplicate}><Copy size={13}/>部品を複製</button><button className="text-button danger" onClick={onDelete}>部品を削除</button></div>
  </div>;
}
type Field = {key:string;label:string;unit?:string;factor?:number;min?:number;hint?:string;timeUnit?:TimeUnit};
const plasmaFields:Field[]=[
  {key:'frequency_hz',label:'RF周波数',unit:'MHz',factor:1e6,min:0},
  {key:'rf_peak_voltage',label:'RF電圧・ピーク',unit:'V',min:0},
  {key:'pressure_pa',label:'ガス圧力',unit:'mTorr',factor:0.1333223684,min:0},
  {key:'gap_m',label:'電極間隔',unit:'mm',factor:0.001,min:0},
  {key:'gas_temperature_k',label:'ガス温度',unit:'K',min:0},
  {key:'cathode_diameter_m',label:'駆動電極直径',unit:'mm',factor:0.001,min:0},
  {key:'area_ratio',label:'接地／駆動 有効面積比',min:0},
  {key:'electron_density_m3',label:'電子密度 nₑ',unit:'m⁻³',min:0},
  {key:'electron_temperature_ev',label:'電子温度 Tₑ',unit:'eV',min:0},
];
const fields:Record<AnalysisKind,Field[]>={
  op:[],dc:[{key:'start',label:'開始値'},{key:'stop',label:'終了値'},{key:'step',label:'増分'}],
  ac:[{key:'start_frequency',label:'開始周波数',unit:'Hz',min:0},{key:'stop_frequency',label:'終了周波数',unit:'Hz',min:0},{key:'points',label:'ポイント数',min:1}],
  transient:[{key:'time_step',label:'出力時間刻み',timeUnit:'us',min:0},{key:'stop_time',label:'終了時間',timeUnit:'ms',min:0}],
  ccp:plasmaFields,global:plasmaFields,global_transient:plasmaFields,radial:plasmaFields,
};
function NumberField({field,value,onChange}:{field:Field;value:Json|undefined;onChange:(v:number)=>void}) {
  const scale=field.factor??1;
  const numeric=typeof value==='number'?value:0;
  return <label>{field.label}<div className="input-unit"><ScientificInput label={field.label} value={numeric} scale={scale} min={field.min} onChange={onChange}/>{field.unit&&<span>{field.unit}</span>}</div></label>;
}
export function AnalysisPanel({analysis,onChange,document}:{analysis:Analysis;onChange:(a:Analysis)=>void;document:CircuitDocument}) {
  const isPlasma=isPlasmaAnalysis(analysis.kind);
  const sourceWaveformDrive=analysis.kind!=='radial'&&document.components.some(c=>c.kind==='PLASMA')&&!isPrescribedPower(analysis);
  function setting(key:string,value:Json){const settings={...analysis.settings,[key]:value};if(key==='power_mode'){delete settings.electron_heating_model;if(value==='prescribed_absorbed'&&typeof settings.absorbed_power_w!=='number')settings.absorbed_power_w=500;if(value==='prescribed_absorbed'&&analysis.kind==='global'){const validation=settings.numerical_validation;settings.numerical_validation=validation&&typeof validation==='object'&&!Array.isArray(validation)?{...validation,enabled:false}:{enabled:false};}}if(key==='gas'&&value==='Ar'&&analysis.kind==='global'){settings.power_mode='rf_coupled';delete settings.electron_heating_model;}if(key==='transport_mode'&&value==='gudmundsson_2000'){settings.axial_edge_factor=null;settings.radial_edge_factor=null;}onChange({...analysis,settings});}
  const values={...defaultSettings[analysis.kind],...analysis.settings};
  return <div className="inspector-section"><div className="panel-heading"><SlidersHorizontal size={16}/><h3>解析条件</h3></div><label>解析方法<select aria-label="解析方法" value={analysis.kind} onChange={e=>{const kind=e.target.value as AnalysisKind;const plasmaSwitch=isPlasma&&isPlasmaAnalysis(kind);const settings=plasmaSwitch?{...clone(defaultSettings[kind]),...analysis.settings}:clone(defaultSettings[kind]);if(kind==='global'&&settings.gas!=='O2'){settings.power_mode='rf_coupled';delete settings.electron_heating_model;}onChange({kind,settings});}}>{Object.entries(analysisNames).map(([kind,name])=><option key={kind} value={kind}>{name}</option>)}</select></label>
    {analysis.kind==='op'&&<p className="muted text-small">回路の定常的な電圧・電流を求めます。</p>}
    {analysis.kind==='dc'&&<label>スイープする独立電源<select aria-label="スイープする独立電源" value={String(values.source??'')} onChange={e=>setting('source',e.target.value)}><option value="">電源を選択</option>{document.components.filter(c=>c.kind==='V'||c.kind==='I').map(c=><option key={c.id} value={c.id}>{c.label}</option>)}</select></label>}
    {analysis.kind==='ac'&&<label>周波数刻み<select aria-label="周波数刻み" value={String(values.variation)} onChange={e=>setting('variation',e.target.value)}><option value="dec">対数・decade</option><option value="oct">対数・octave</option><option value="lin">線形</option></select></label>}
    {isPlasma&&document.components.some(c=>c.kind==='PLASMA')&&<><label>RF電源<select aria-label="RF電源" value={String(values.rf_source_id??'')} onChange={e=>setting('rf_source_id',e.target.value)}><option value="">電圧源が1つなら自動選択</option>{document.components.filter(c=>c.kind==='V').map(c=><option key={c.id} value={c.id}>{c.label} ({c.id})</option>)}</select></label><NumberField field={{key:'source_reference_impedance_ohm',label:'電源基準インピーダンス',unit:'Ω'}} value={values.source_reference_impedance_ohm??50} onChange={v=>setting('source_reference_impedance_ohm',v)}/></>}
    {isPlasma&&<><label>単一ガス<select aria-label="単一ガス" value={String(values.gas)} onChange={e=>setting('gas',e.target.value)}><option value="Ar">Ar — アルゴン</option><option value="O2">O₂ — 酸素</option>{!['Ar','O2'].includes(String(values.gas))&&<option value={String(values.gas)} disabled>{String(values.gas)} — 保存済み条件</option>}</select></label><div className="info-box">{isPrescribedPower(analysis)?'総吸収プラズマ電力を指定する0Dモデルです。':'RF電圧は電極ピーク値。外部回路を有効にすると電源側ピーク値です。'}{['global','global_transient'].includes(analysis.kind)&&' nₑ・Tₑは初期推定値です。'}</div></>}
    {sourceWaveformDrive&&<p className="info-box">RF周波数・ピーク電圧・第2RF・パルス包絡は、回路図のRF電圧源を選択して「電源波形・2周波数RF」で設定します。</p>}
    <div className="form-grid">{fields[analysis.kind].filter(field=>!(isPrescribedPower(analysis)||sourceWaveformDrive)||!['frequency_hz','rf_peak_voltage'].includes(field.key)).map(field=>field.timeUnit?<TimeInput key={`${analysis.kind}-${field.key}`} label={field.label} value={values[field.key]} defaultUnit={field.timeUnit} min={field.min} onChange={v=>setting(field.key,v)}/>:<NumberField key={`${analysis.kind}-${field.key}`} field={field} value={values[field.key]} onChange={v=>setting(field.key,v)}/>)}</div>
    {isPlasma&&<PlasmaControls kind={analysis.kind} values={values} set={setting} componentMode={document.components.some(c=>c.kind==='PLASMA')}/>}
    <details className="advanced"><summary>詳細な解析条件</summary><p className="muted text-small">SI単位で指定します。設定値は計算履歴に保存されます。</p><JsonEditor label="settings" value={analysis.settings} rows={8} onApply={v=>{if(!v||Array.isArray(v)||typeof v!=='object')throw new Error('JSONオブジェクトを指定してください。');onChange({...analysis,settings:v as Record<string,Json>});}}/></details>
  </div>;
}
export function DocumentPanel({document,onChange}:{document:CircuitDocument;onChange:(doc:CircuitDocument)=>void}) {
  return <div className="inspector-section"><div className="panel-heading"><FileCode2 size={16}/><h3>回路とモデル</h3></div><label>回路名<input value={document.name} onChange={e=>onChange({...document,name:e.target.value})}/></label><label>メモ<textarea value={document.description} rows={2} onChange={e=>onChange({...document,description:e.target.value})}/></label><details className="advanced"><summary>SPICEモデル・サブ回路</summary><JsonEditor label="モデル定義" value={document.models} rows={9} onApply={v=>{if(!Array.isArray(v)||v.some(m=>typeof m?.name!=='string'||typeof m?.definition!=='string'))throw new Error('name と definition を持つ配列を指定してください。');onChange({...document,models:v});}}/></details><details className="advanced"><summary>回路ドキュメントを編集</summary><JsonEditor label="回路JSON" value={document} rows={16} onApply={v=>{const d=v as CircuitDocument;if(!d||!Array.isArray(d.components)||!Array.isArray(d.wires)||!Array.isArray(d.models)||typeof d.name!=='string')throw new Error('回路ドキュメント形式を確認してください。');if(new Set(d.components.map(c=>c.id)).size!==d.components.length)throw new Error('部品IDが重複しています。');onChange(d);}}/></details></div>;
}

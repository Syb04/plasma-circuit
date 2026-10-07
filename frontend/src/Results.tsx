import { useEffect, useMemo, useState } from 'react';
import { AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, Legend, Brush } from 'recharts';
import { Download, Activity, AlertTriangle, CheckCircle2, FileText, LoaderCircle, Clock3 } from 'lucide-react';
import type { Json, Result, Run } from './types';
import { engineering, statusNames, analysisLabel, isPrescribedPower, downloadApi } from './types';

const colors=Array.from({length:6},(_,index)=>`var(--series-${index+1})`);
const axisTick={fontSize:11,fill:'var(--text-muted)'};
const tooltipStyle={background:'var(--surface)',color:'var(--text)',border:'1px solid var(--border)',borderRadius:8,fontSize:12};
const summaryLabels:Record<string,{label:string;unit:string}>={
  electron_density_m3:{label:'電子密度 nₑ',unit:'m⁻³'},electron_temperature_ev:{label:'電子温度 Tₑ',unit:'eV'},
  gas_temperature_k:{label:'ガス温度',unit:'K'},pressure_pa:{label:'圧力',unit:'Pa'},
  dc_self_bias_v:{label:'DC自己バイアス',unit:'V'},rf_current_rms_a:{label:'RF電流 RMS',unit:'A'},
  absorbed_power_w:{label:'総吸収プラズマ電力',unit:'W'},electron_absorbed_power_w:{label:'電子吸収電力',unit:'W'},
  electrode_absorbed_power_w:{label:'電極側吸収電力',unit:'W'},electron_heating_w:{label:'電子加熱電力',unit:'W'},
  mean_ion_energy_ev:{label:'平均入射イオンエネルギー',unit:'eV'},total_ion_flux_m2_s:{label:'入射イオンフラックス',unit:'m⁻² s⁻¹'},
};
function domainWarnings(value:Json|undefined):string[]{
  if(!value||typeof value!=='object'||Array.isArray(value))return [];
  return [...new Set(Object.entries(value).flatMap(([key,item])=>['domain_warnings','warnings'].includes(key)&&Array.isArray(item)?item.filter((v):v is string=>typeof v==='string'):domainWarnings(item)))];
}
function scaledUnit(max:number,unit:string):{factor:number;label:string} {
  if (!['s','Hz','V','A','C','W','F','H','Ω'].includes(unit)||!max) return {factor:1,label:unit};
  const exponent=Math.max(-12,Math.min(9,Math.floor(Math.log10(max)/3)*3));
  const prefixes:Record<number,string>={'-12':'p','-9':'n','-6':'µ','-3':'m','0':'','3':'k','6':'M','9':'G'};
  return {factor:10**exponent,label:prefixes[exponent]+unit};
}
function displayValue(value:Json):string {if(typeof value==='number')return Math.abs(value)>1e5||Math.abs(value)<1e-3&&value!==0?value.toExponential(4):String(Number(value.toPrecision(6)));if(typeof value==='object')return JSON.stringify(value);return String(value);}
export function ResultTable({table,index}:{table:Json;index:number}) {
  if(!table||typeof table!=='object'||Array.isArray(table)||!Array.isArray(table.columns)||!Array.isArray(table.rows))return <pre>{JSON.stringify(table,null,2)}</pre>;
  const columns=table.columns.map(column=>String(column));
  return <div className="numeric-table"><h3>{typeof table.name==='string'?table.name:`数値テーブル ${index+1}`}</h3><table><thead><tr>{columns.map((column,i)=><th key={`${column}-${i}`}>{column}</th>)}</tr></thead><tbody>{table.rows.map((row,i)=><tr key={i}>{columns.map((column,j)=>{const value=Array.isArray(row)?row[j]:row&&typeof row==='object'?row[column]:j===0?row:undefined;return <td key={`${column}-${j}`}>{value===undefined?'—':displayValue(value)}</td>;})}</tr>)}</tbody></table></div>;
}
function RfDiagnostics({data}:{data:Record<string,Json>}){
  const planes=Array.isArray(data.measurement_planes)?data.measurement_planes.filter(p=>p&&typeof p==='object'&&!Array.isArray(p)) as Record<string,Json>[]:[];
  if(!planes.length)return null;
  return <div className="rf-panel research-card"><div className="flex-row"><h3>RF診断・測定面</h3><span className="muted text-small">{engineering(Number(data.frequency_hz),'Hz')} · {String(data.cycles_used)} 周期</span></div><p className="muted text-small">基本波ピーク・フェーザからZと位相を計算します。平均電力は保存波形のV(t)I(t)を積分します。</p>{planes.map((plane,index)=>{const impedance=plane.impedance_ohm&&typeof plane.impedance_ohm==='object'&&!Array.isArray(plane.impedance_ohm)?plane.impedance_ohm:{};const cards=[{name:'Z 実部',value:impedance.real,unit:'Ω'},{name:'Z 虚部',value:impedance.imag,unit:'Ω'},{name:'V−I 位相',value:plane.phase_deg,unit:'°'},{name:'平均実電力',value:plane.mean_power_w,unit:'W'},{name:'基本波電力',value:plane.fundamental_power_w,unit:'W'},{name:'電圧 RMS',value:plane.voltage_rms_v,unit:'V'},{name:'電流 RMS',value:plane.current_rms_a,unit:'A'},{name:'電流 THD',value:plane.current_thd,unit:''},{name:'力率',value:plane.power_factor,unit:''},...(plane.forward_power_w===undefined?[]:[{name:'進行波電力',value:plane.forward_power_w,unit:'W'},{name:'反射波電力',value:plane.reflected_power_w,unit:'W'}])];return <div className="rf-plane" key={index}><h4>{String(plane.name)}</h4><div className="summary-grid">{cards.filter(card=>typeof card.value==='number').map(card=><div className="summary-card" key={card.name}><span>{card.name}</span><strong>{card.unit==='°'?`${displayValue(card.value!)}°`:engineering(Number(card.value),card.unit)}</strong></div>)}</div>{Array.isArray(plane.harmonics)&&<details className="result-detail"><summary>基本波・高調波スペクトル</summary><ResultTable index={index} table={{name:'RF高調波',columns:['order','frequency_hz','voltage_peak_v','current_peak_a','voltage_phase_deg','current_phase_deg','power_w'],rows:plane.harmonics}}/></details>}</div>;})}<details className="result-detail"><summary>診断の仮定・品質</summary><pre>{JSON.stringify({assumptions:data.assumptions,quality:data.quality},null,2)}</pre></details></div>;
}
function NumericalValidation({data}:{data:Json|undefined}){
  if(!data||typeof data!=='object'||Array.isArray(data))return null;
  return <details className="result-detail" open><summary><CheckCircle2 size={15}/>数値精細化の検証</summary>{typeof data.passed==='boolean'&&<div className={data.passed?'info-box':'warning-box'}>{data.passed?'点数・周期数を増やした計算との比較が許容差を満たしました。':'精細化の差または追加計算の診断を確認してください。'}</div>}<p className="muted text-small">数値計算の安定性を確認します。実験値との参照比較は「研究・比較」で行えます。</p><pre>{JSON.stringify(data,null,2)}</pre></details>;
}
function IedfPanel({result}:{result:Result}){
  const axis=result.axis?.values??[];
  if(!axis.length)return <div className="research-card"><h3>イオンエネルギー分布（IEDF）</h3><pre>{JSON.stringify(result,null,2)}</pre></div>;
  const rows=axis.map((energy,i)=>{const row:Record<string,number>={energy};result.signals.forEach((signal,j)=>row[`s${j}`]=signal.values[i]);return row;});
  return <div className="research-card iedf-panel"><h3>イオンエネルギー分布（IEDF）</h3><p className="muted text-small">シース波形で計算した粒子軌道のエネルギーヒストグラムです。PDFは電極に到達した粒子で正規化し、フラックスは到達率を含みます。</p>{!result.converged&&<p className="warning-box">未到達の粒子またはフラックス不足があります。IEDFの収束・粒子診断を確認してください。</p>}<div className="summary-grid">{['mean_ion_energy_ev','total_ion_flux_m2_s'].filter(key=>typeof result.summary[key]==='number').map(key=><div className="summary-card" key={key}><span>{summaryLabels[key].label}</span><strong>{engineering(Number(result.summary[key]),summaryLabels[key].unit)}</strong></div>)}</div>{domainWarnings(result.diagnostics).map((warning,i)=><p className="warning-box" key={i}>{warning}</p>)}<div className="chart-area" role="img" aria-label="IEDFエネルギーヒストグラム"><ResponsiveContainer width="100%" height="100%"><LineChart data={rows} margin={{top:15,right:25,left:15,bottom:20}}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3"/><XAxis type="number" dataKey="energy" domain={['dataMin','dataMax']} tick={axisTick} label={{value:`入射エネルギー [${result.axis?.unit}]`,position:'insideBottom',offset:-12,fontSize:11,fill:'var(--text-muted)'}}/><YAxis tick={axisTick} tickFormatter={v=>Number(v).toPrecision(3)} label={{value:'確率密度 [eV⁻¹]',angle:-90,position:'insideLeft',fontSize:11,fill:'var(--text-muted)'}}/><Tooltip contentStyle={tooltipStyle}/><Legend/>{result.signals.map((signal,i)=><Line key={signal.name} dataKey={`s${i}`} name={`${signal.name} [${signal.unit}]`} stroke={colors[i%colors.length]} dot={false} type="step" isAnimationActive={false}/>)}</LineChart></ResponsiveContainer></div>{result.tables?.map((table,i)=><ResultTable key={i} table={table} index={i}/>)}<details className="result-detail"><summary>IEDFの条件・診断</summary><pre>{JSON.stringify({summary:result.summary,model_metadata:result.model_metadata,diagnostics:result.diagnostics,logs:result.logs},null,2)}</pre></details></div>;
}
export function RunStatusView({run}:{run:Run|null}) {
  if(!run)return <div className="result-placeholder"><div className="placeholder-icon"><Activity size={30}/></div><h2>計算結果をここに表示</h2><p>回路と解析条件を設定し、計算を実行してください。<br/>過去の結果は「計算履歴」から開けます。</p></div>;
  const busy=run.status==='queued'||run.status==='running';
  return <div className={`result-placeholder ${busy?'':'error-result'}`}><div className="placeholder-icon">{busy?<LoaderCircle size={30} className="spin"/>:run.status==='canceled'?<Clock3 size={30}/>:<AlertTriangle size={30}/>}</div><h2>{statusNames[run.status]}</h2><p>{busy?'保存した回路のスナップショットで解析しています。画面は引き続き操作できます。':run.error||'計算は停止しました。'}</p><span className="muted text-small">回路 rev.{run.circuit_revision} · {run.employee_id} · {analysisLabel(run.analysis)}</span></div>;
}
export default function Results({run,onMessage}:{run:Run|null;onMessage:(message:string)=>void}) {
  const result=run?.result;
  const [mode,setMode]=useState<'waveform'|'xy'>('waveform');
  const [unit,setUnit]=useState('');
  const [hidden,setHidden]=useState<Set<string>>(new Set());
  const [xSignal,setXSignal]=useState('');
  const [ySignal,setYSignal]=useState('');
  useEffect(()=>{
    if (!result) return;
    setUnit(result.signals.find(s=>s.unit==='V')?.unit??result.signals[0]?.unit??'');setHidden(new Set());setMode('waveform');
    const charge=result.signals.find(s=>s.unit==='C');
    const branch=charge?.name.match(/^Q\(([^)]+:branch\d+)\)( magnitude)?$/);
    const branchVoltage=branch?result.signals.find(s=>s.unit==='V'&&s.name===`V(${branch[1]})${branch[2]??''}`):undefined;
    setXSignal(branchVoltage?.name??result.signals.find(s=>s.unit==='V')?.name??result.signals[0]?.name??'');setYSignal(charge?.name??result.signals[1]?.name??'');
  },[run?.id,result]);
  const signals=result?.signals??[];
  const visibleSignals=signals.filter(s=>s.unit===unit&&!hidden.has(s.name));
  const axisValues=result?.axis?.values??[];
  const xScale=scaledUnit(axisValues.reduce((max,value)=>Number.isFinite(value)?Math.max(max,Math.abs(value)):max,0),result?.axis?.unit??'');
  const maximum=visibleSignals.reduce((max,s)=>s.values.reduce((m,v)=>Math.max(m,Math.abs(v)),max),0);
  const yScale=scaledUnit(maximum,unit);
  const stride=Math.max(1,Math.ceil(axisValues.length/1600));
  const chartData=useMemo(()=>{
    if(!result?.axis)return [];
    const rows:Record<string,number>[]=[];
    for(let i=0;i<result.axis.values.length;i+=stride){const row:Record<string,number>={x:result.axis.values[i]/xScale.factor};visibleSignals.forEach((s,j)=>{if(Number.isFinite(s.values[i]))row[`s${j}`]=s.values[i]/yScale.factor;});rows.push(row);}
    if(result.axis.values.length>1&&(result.axis.values.length-1)%stride!==0){const i=result.axis.values.length-1;const row:Record<string,number>={x:result.axis.values[i]/xScale.factor};visibleSignals.forEach((s,j)=>{if(Number.isFinite(s.values[i]))row[`s${j}`]=s.values[i]/yScale.factor;});rows.push(row);}
    return rows;
  },[result,visibleSignals.map(s=>s.name).join('|'),stride,xScale.factor,yScale.factor]);
  const xSource=signals.find(s=>s.name===xSignal),ySource=signals.find(s=>s.name===ySignal);
  const xyXScale=scaledUnit(xSource?.values.reduce((m,v)=>Math.max(m,Math.abs(v)),0)??0,xSource?.unit??'');
  const xyYScale=scaledUnit(ySource?.values.reduce((m,v)=>Math.max(m,Math.abs(v)),0)??0,ySource?.unit??'');
  const xyData=useMemo(()=>{
    if(!xSource||!ySource)return [];
    const rows=[];const length=Math.min(xSource.values.length,ySource.values.length);const step=Math.max(1,Math.ceil(length/1600));
    for(let i=0;i<length;i+=step)rows.push({x:xSource.values[i]/xyXScale.factor,y:ySource.values[i]/xyYScale.factor});return rows;
  },[xSource,ySource,xyXScale.factor,xyYScale.factor]);
  if(!run||!result)return <RunStatusView run={run}/>;
  const isPlasma=['ccp','global','global_transient','radial'].includes(result.kind)||!!result.rf_diagnostics;
  const summaries=Object.entries(result.summary??{});
  const numericSummaries=summaries.filter(([,v])=>typeof v==='number').slice(0,8);
  const prescribedPower=isPrescribedPower(run.analysis);
  const plotLabel=run.analysis.kind==='radial'?'径方向分布':run.analysis.kind==='global_transient'?'時間発展':'波形';
  const warnings=domainWarnings(result.diagnostics);
  const canPlot=axisValues.length>0&&signals.length>0;
  function toggleSignal(name:string){setHidden(previous=>{const next=new Set(previous);if(next.has(name))next.delete(name);else next.add(name);return next;});}
  async function exportCsv(){try{const response=await fetch(`/api/runs/${run!.id}/export.csv`);if(!response.ok)throw new Error('CSVの取得に失敗しました。');const blob=await response.blob();const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`result-${run!.id}.csv`;link.click();URL.revokeObjectURL(url);}catch(e){onMessage(e instanceof Error?e.message:String(e));}}
  return <div className="results-content"><div className="results-heading"><div><div className="eyebrow">SIMULATION RESULT</div><h2>{analysisLabel(run.analysis)}</h2><p>rev.{run.circuit_revision} · {new Date(run.created_at).toLocaleString('ja-JP')} · 社員番号 {run.employee_id}</p></div><div className="flex-row"><button className="button secondary" onClick={exportCsv}><Download size={15}/>CSV保存</button><button className="button secondary" onClick={()=>void downloadApi(`/runs/${run.id}/package`,`model-${run.id}.json`).catch(e=>onMessage(e.message))}><Download size={15}/>モデルパッケージ</button></div></div>
    <div className={`result-health ${result.converged?'ok':'warning'}`}>{result.converged?<CheckCircle2 size={17}/>:<AlertTriangle size={17}/>}<strong>{result.converged?'解析完了':'収束条件を満たしていません'}</strong>{!result.converged&&<span>診断と計算ログを確認してください。</span>}</div>
    {isPlasma&&<div className="warning-box">研究用の縮約モデルです。実験値との妥当性検証は未完了です。モデルの仮定・反応データ・適用範囲を確認して利用してください。</div>}
    {prescribedPower&&<div className="info-box">指定した総吸収プラズマ電力（電子・イオンの吸収を含む）に対する{run.analysis.kind==='global_transient'?'時間発展':'定常'}0D反応計算です。反応・壁損失・エネルギー収支を使用します。</div>}
    {numericSummaries.length>0&&<div className="summary-grid">{numericSummaries.map(([key,value])=><div className="summary-card" key={key}><span title={key}>{summaryLabels[key]?.label??key.replaceAll('_',' ')}</span><strong>{summaryLabels[key]?engineering(Number(value),summaryLabels[key].unit):displayValue(value)}</strong></div>)}</div>}
    {warnings.length>0&&<details className="result-detail" open><summary><AlertTriangle size={15}/>モデルの適用範囲・警告</summary>{warnings.map((warning,i)=><p className="warning-box" key={i}>{warning}</p>)}</details>}
    {result.rf_diagnostics&&<RfDiagnostics data={result.rf_diagnostics}/>}
    <NumericalValidation data={result.diagnostics?.numerical_validation}/>
    {result.diagnostics?.electromagnetics_validity&&<details className="result-detail" open><summary><AlertTriangle size={15}/>電磁的な適用範囲</summary><pre>{JSON.stringify(result.diagnostics.electromagnetics_validity,null,2)}</pre></details>}
    {result.iedf&&<IedfPanel result={result.iedf}/>}
    {canPlot&&<div className="chart-card"><div className="chart-header"><div className="segmented"><button className={mode==='waveform'?'active':''} onClick={()=>setMode('waveform')}>{plotLabel}</button><button className={mode==='xy'?'active':''} onClick={()=>setMode('xy')}>X–Y / Q–V</button></div>{mode==='waveform'?<select aria-label="表示する信号単位" value={unit} onChange={e=>setUnit(e.target.value)}>{[...new Set(signals.map(s=>s.unit))].map(u=><option value={u} key={u}>{u||'無次元'} の信号</option>)}</select>:<div className="xy-select"><label>X<select aria-label="X軸の信号" value={xSignal} onChange={e=>setXSignal(e.target.value)}>{signals.map(s=><option key={s.name} value={s.name}>{s.name} [{s.unit}]</option>)}</select></label><label>Y<select aria-label="Y軸の信号" value={ySignal} onChange={e=>setYSignal(e.target.value)}>{signals.map(s=><option key={s.name} value={s.name}>{s.name} [{s.unit}]</option>)}</select></label></div>}</div>
      <div className="chart-area"><ResponsiveContainer width="100%" height="100%">{mode==='waveform'?
        <LineChart data={chartData} margin={{top:25,right:25,left:12,bottom:20}}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3"/>
          <XAxis dataKey="x" type="number" domain={['dataMin','dataMax']} scale={run.analysis.kind==='ac'&&run.analysis.settings.variation!=='lin'?'log':'linear'} stroke="var(--text-muted)" axisLine={{stroke:'var(--border)'}} tickLine={{stroke:'var(--border)'}} tickFormatter={v=>String(Number(Number(v).toPrecision(3)))} tick={axisTick} label={{value:`${result.axis?.name??'x'} [${xScale.label}]`,position:'insideBottom',offset:-12,fontSize:12,fill:'var(--text-muted)'}}/>
          <YAxis stroke="var(--text-muted)" axisLine={{stroke:'var(--border)'}} tickLine={{stroke:'var(--border)'}} tick={axisTick} tickFormatter={v=>Number(v).toPrecision(3)} label={{value:yScale.label,angle:-90,position:'insideLeft',fontSize:12,fill:'var(--text-muted)'}}/>
          <Tooltip formatter={(v:number|string,name:string)=>[typeof v==='number'?Number(v.toPrecision(6)):v,name]} labelFormatter={v=>`${Number(v).toPrecision(5)} ${xScale.label}`} contentStyle={tooltipStyle} labelStyle={{color:'var(--text)'}} itemStyle={{color:'var(--text)'}} cursor={{stroke:'var(--text-muted)',strokeDasharray:'3 3'}}/>
          {visibleSignals.map((s,i)=><Line key={s.name} type="linear" dataKey={`s${i}`} name={s.name} stroke={colors[signals.indexOf(s)%colors.length]} strokeWidth={1.8} dot={false} activeDot={{fill:'var(--bg)',stroke:colors[signals.indexOf(s)%colors.length]}} isAnimationActive={false}/>)}
          <Brush dataKey="x" height={22} fill="var(--surface)" stroke="var(--text-muted)" travellerWidth={7} tickFormatter={()=>''}/>
        </LineChart>:
        <ScatterChart margin={{top:25,right:25,left:12,bottom:20}}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3"/>
          <XAxis type="number" dataKey="x" name={xSignal} stroke="var(--text-muted)" axisLine={{stroke:'var(--border)'}} tickLine={{stroke:'var(--border)'}} tick={axisTick} domain={['auto','auto']} label={{value:`${xSignal} [${xyXScale.label}]`,position:'insideBottom',offset:-12,fontSize:12,fill:'var(--text-muted)'}}/>
          <YAxis type="number" dataKey="y" name={ySignal} stroke="var(--text-muted)" axisLine={{stroke:'var(--border)'}} tickLine={{stroke:'var(--border)'}} tick={axisTick} label={{value:xyYScale.label,angle:-90,position:'insideLeft',fontSize:12,fill:'var(--text-muted)'}}/>
          <Tooltip cursor={{stroke:'var(--text-muted)',strokeDasharray:'3 3'}} contentStyle={tooltipStyle} labelStyle={{color:'var(--text)'}} itemStyle={{color:'var(--text)'}}/>
          <Scatter data={xyData} name={`${ySignal} / ${xSignal}`} fill={colors[0]} line={{stroke:colors[0]}} shape={()=><></>} isAnimationActive={false}/>
        </ScatterChart>}
      </ResponsiveContainer></div>
      {mode==='waveform'&&<div className="trace-list">{signals.filter(s=>s.unit===unit).map(s=><button key={s.name} className={hidden.has(s.name)?'hidden-trace':''} onClick={()=>toggleSignal(s.name)}><span style={{background:colors[signals.indexOf(s)%colors.length]}}/>{s.name}</button>)}</div>}
      <p className="chart-note">{stride>1?`表示は最大約1,600点に間引いています（保存点数 ${axisValues.length.toLocaleString()}）。`:`保存点数 ${axisValues.length.toLocaleString()}。`} CSVには保存された全サンプルを出力します。波形下の範囲選択で拡大できます。</p>
    </div>}
    {!canPlot&&summaries.length>0&&<div className="numeric-table"><h3>{prescribedPower?'定常0D反応計算・数値結果':'動作点・数値結果'}</h3><table><thead><tr><th>項目</th><th>値</th></tr></thead><tbody>{summaries.map(([key,value])=><tr key={key}><td>{key}</td><td>{displayValue(value)}</td></tr>)}</tbody></table></div>}
    {canPlot&&summaries.length>0&&<details className="result-detail"><summary><FileText size={15}/>全ての主要指標・summaryキー</summary><ResultTable table={{name:'主要指標（参照CSVのmetricに使用）',columns:['metric','value'],rows:summaries.map(([key,value])=>[key,value])}} index={0}/></details>}
    {(result.tables?.length??0)>0&&<details className="result-detail" open={!canPlot}><summary><FileText size={15}/>数値テーブル</summary>{result.tables?.map((table,index)=><ResultTable key={index} table={table} index={index}/>)}</details>}
    <details className="result-detail" open={isPlasma}><summary><FileText size={15}/>モデルの仮定・データ・診断</summary><div className="metadata-columns"><div><h4>モデル情報</h4><pre>{JSON.stringify(result.model_metadata??{},null,2)}</pre></div><div><h4>収束・収支の診断</h4><pre>{JSON.stringify(result.diagnostics??{},null,2)}</pre></div></div></details>
    <details className="result-detail"><summary><FileText size={15}/>ソルバー・ネットリスト・計算条件・ログ</summary><h4>ソルバー</h4><pre>{JSON.stringify(result.solver??{},null,2)}</pre><h4>保存された解析条件</h4><pre>{JSON.stringify(run.analysis,null,2)}</pre><h4>生成ネットリスト</h4><pre>{result.netlist||'提供されていません'}</pre><h4>ログ</h4><pre>{result.logs?.join('\n')||'ログなし'}</pre></details>
  </div>;
}

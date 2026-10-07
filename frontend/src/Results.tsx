import { useEffect, useMemo, useState } from 'react';
import { AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, Legend, Brush } from 'recharts';
import { Download, Activity, AlertTriangle, CheckCircle2, FileText, LoaderCircle, Clock3 } from 'lucide-react';
import type { Json, Result, Run } from './types';
import { engineering, statusNames, analysisLabel, isPrescribedPower } from './types';

const colors=Array.from({length:6},(_,index)=>`var(--series-${index+1})`);
const axisTick={fontSize:11,fill:'var(--text-muted)'};
const tooltipStyle={background:'var(--surface)',color:'var(--text)',border:'1px solid var(--border)',borderRadius:8,fontSize:12};
function scaledUnit(max:number,unit:string):{factor:number;label:string} {
  if (!['s','Hz','V','A','C','W','F','H','Ω'].includes(unit)||!max) return {factor:1,label:unit};
  const exponent=Math.max(-12,Math.min(9,Math.floor(Math.log10(max)/3)*3));
  const prefixes:Record<number,string>={'-12':'p','-9':'n','-6':'µ','-3':'m','0':'','3':'k','6':'M','9':'G'};
  return {factor:10**exponent,label:prefixes[exponent]+unit};
}
function displayValue(value:Json):string {if(typeof value==='number')return Math.abs(value)>1e5||Math.abs(value)<1e-3&&value!==0?value.toExponential(4):String(Number(value.toPrecision(6)));if(typeof value==='object')return JSON.stringify(value);return String(value);}
function ResultTable({table,index}:{table:Json;index:number}) {
  if(!table||typeof table!=='object'||Array.isArray(table)||!Array.isArray(table.columns)||!Array.isArray(table.rows))return <pre>{JSON.stringify(table,null,2)}</pre>;
  const columns=table.columns.map(column=>String(column));
  return <div className="numeric-table"><h3>{typeof table.name==='string'?table.name:`数値テーブル ${index+1}`}</h3><table><thead><tr>{columns.map((column,i)=><th key={`${column}-${i}`}>{column}</th>)}</tr></thead><tbody>{table.rows.map((row,i)=><tr key={i}>{columns.map((column,j)=>{const value=Array.isArray(row)?row[j]:row&&typeof row==='object'?row[column]:j===0?row:undefined;return <td key={`${column}-${j}`}>{value===undefined?'—':displayValue(value)}</td>;})}</tr>)}</tbody></table></div>;
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
  const isPlasma=result.kind==='ccp'||result.kind==='global';
  const summaries=Object.entries(result.summary??{});
  const numericSummaries=summaries.filter(([,v])=>typeof v==='number').slice(0,8);
  const prescribedPower=isPrescribedPower(run.analysis);
  const canPlot=!prescribedPower&&axisValues.length>0&&signals.length>0;
  function toggleSignal(name:string){setHidden(previous=>{const next=new Set(previous);if(next.has(name))next.delete(name);else next.add(name);return next;});}
  async function exportCsv(){try{const response=await fetch(`/api/runs/${run!.id}/export.csv`);if(!response.ok)throw new Error('CSVの取得に失敗しました。');const blob=await response.blob();const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`result-${run!.id}.csv`;link.click();URL.revokeObjectURL(url);}catch(e){onMessage(e instanceof Error?e.message:String(e));}}
  return <div className="results-content"><div className="results-heading"><div><div className="eyebrow">SIMULATION RESULT</div><h2>{analysisLabel(run.analysis)}</h2><p>rev.{run.circuit_revision} · {new Date(run.created_at).toLocaleString('ja-JP')} · 社員番号 {run.employee_id}</p></div><button className="button secondary" onClick={exportCsv}><Download size={15}/>CSV保存</button></div>
    <div className={`result-health ${result.converged?'ok':'warning'}`}>{result.converged?<CheckCircle2 size={17}/>:<AlertTriangle size={17}/>}<strong>{result.converged?'解析完了':'収束条件を満たしていません'}</strong>{!result.converged&&<span>診断と計算ログを確認してください。</span>}</div>
    {isPlasma&&<div className="warning-box">研究用の縮約モデルです。実験値との妥当性検証は未完了です。モデルの仮定・反応データ・適用範囲を確認して利用してください。</div>}
    {prescribedPower&&<div className="info-box">指定した吸収電子電力に対する定常0D反応計算です。文献モデルの反応・壁損失・電子エネルギー収支を使用します。RF電圧・電流波形は計算しません。</div>}
    {numericSummaries.length>0&&<div className="summary-grid">{numericSummaries.map(([key,value])=><div className="summary-card" key={key}><span title={key}>{key.replaceAll('_',' ')}</span><strong>{displayValue(value)}</strong></div>)}</div>}
    {canPlot&&<div className="chart-card"><div className="chart-header"><div className="segmented"><button className={mode==='waveform'?'active':''} onClick={()=>setMode('waveform')}>波形</button><button className={mode==='xy'?'active':''} onClick={()=>setMode('xy')}>X–Y / Q–V</button></div>{mode==='waveform'?<select aria-label="表示する信号単位" value={unit} onChange={e=>setUnit(e.target.value)}>{[...new Set(signals.map(s=>s.unit))].map(u=><option value={u} key={u}>{u||'無次元'} の信号</option>)}</select>:<div className="xy-select"><label>X<select value={xSignal} onChange={e=>setXSignal(e.target.value)}>{signals.map(s=><option key={s.name} value={s.name}>{s.name} [{s.unit}]</option>)}</select></label><label>Y<select value={ySignal} onChange={e=>setYSignal(e.target.value)}>{signals.map(s=><option key={s.name} value={s.name}>{s.name} [{s.unit}]</option>)}</select></label></div>}</div>
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
    {(result.tables?.length??0)>0&&<details className="result-detail" open={!canPlot}><summary><FileText size={15}/>数値テーブル</summary>{result.tables?.map((table,index)=><ResultTable key={index} table={table} index={index}/>)}</details>}
    <details className="result-detail" open={isPlasma}><summary><FileText size={15}/>モデルの仮定・データ・診断</summary><div className="metadata-columns"><div><h4>モデル情報</h4><pre>{JSON.stringify(result.model_metadata??{},null,2)}</pre></div><div><h4>収束・収支の診断</h4><pre>{JSON.stringify(result.diagnostics??{},null,2)}</pre></div></div></details>
    <details className="result-detail"><summary><FileText size={15}/>ソルバー・ネットリスト・計算条件・ログ</summary><h4>ソルバー</h4><pre>{JSON.stringify(result.solver??{},null,2)}</pre><h4>保存された解析条件</h4><pre>{JSON.stringify(run.analysis,null,2)}</pre><h4>生成ネットリスト</h4><pre>{result.netlist||'提供されていません'}</pre><h4>ログ</h4><pre>{result.logs?.join('\n')||'ログなし'}</pre></details>
  </div>;
}

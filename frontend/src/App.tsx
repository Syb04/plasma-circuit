import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowLeft, ArrowRight, BookOpen, ChevronDown, ChevronRight, CircuitBoard, Clock3, Copy, FolderOpen, History, LoaderCircle, Play, Plus, Save, Search, Square, Undo2, Redo2, X, Zap, AlertCircle, CheckCircle2, Download } from 'lucide-react';
import Schematic, { CircuitSymbol, PlasmaDiagram } from './Schematic';
import { AnalysisPanel, ComponentInspector, DocumentPanel } from './Inspector';
import Results from './Results';
import type { Analysis, AnalysisKind, CatalogComponent, CircuitDocument, Component, Preset, Run, SavedCircuit, CircuitSummary } from './types';
import { api, ApiError, clone, createId, defaultSettings, emptyDocument, statusNames, analysisLabel, isPrescribedPower } from './types';
import { useTheme, type ThemePreference } from './theme';

const fallbackCatalog:CatalogComponent[]=[
  {kind:'R',label:'抵抗',category:'受動素子',ports:['p','n'],parameters:{value:1000}},
  {kind:'C',label:'コンデンサ',category:'受動素子',ports:['p','n'],parameters:{value:1e-9}},
  {kind:'L',label:'インダクタ',category:'受動素子',ports:['p','n'],parameters:{value:1e-6}},
  {kind:'V',label:'電圧源',category:'電源',ports:['p','n'],parameters:{dc:5,ac_magnitude:1}},
  {kind:'I',label:'電流源',category:'電源',ports:['p','n'],parameters:{dc:0.001}},
  {kind:'GND',label:'グラウンド',category:'接続',ports:['g'],parameters:{}},
  {kind:'JUNCTION',label:'接続点',category:'接続',ports:['p'],parameters:{}},
  {kind:'EDD',label:'数式定義素子',category:'数式・モデル',ports:['p1','n1'],parameters:{branches:[{positive:'p1',negative:'n1',current:'V1/R',charge:'C0*V1'}],parameters:{R:1000,C0:1e-9},intermediates:{}}},
];
function relativeTime(value:string){const date=new Date(value);return date.toLocaleString('ja-JP',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});}
function readEmployee(){try{return localStorage.getItem('plasma-circuit.employee-id')??'';}catch{return '';}}
function errorMessage(e:unknown){return e instanceof Error?e.message:String(e);}
function isBusy(run:Run|null){return !!run&&(run.status==='queued'||run.status==='running');}
export default function App() {
  const [theme, setTheme] = useTheme();
  const [doc,setDoc]=useState<CircuitDocument>(emptyDocument);
  const docRef=useRef(doc);docRef.current=doc;
  const [analysis,setAnalysis]=useState<Analysis>({kind:'transient',settings:clone(defaultSettings.transient)});
  const [saved,setSaved]=useState<SavedCircuit|null>(null);
  const [dirty,setDirty]=useState(false);
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [past,setPast]=useState<CircuitDocument[]>([]),[future,setFuture]=useState<CircuitDocument[]>([]);
  const [employee,setEmployee]=useState(readEmployee),[employeeError,setEmployeeError]=useState(false);
  const [catalog,setCatalog]=useState<CatalogComponent[]>(fallbackCatalog);
  const [presets,setPresets]=useState<Preset[]>([]),[circuits,setCircuits]=useState<CircuitSummary[]>([]),[runs,setRuns]=useState<Run[]>([]);
  const [selectedRun,setSelectedRun]=useState<Run|null>(null),[activeRun,setActiveRun]=useState<Run|null>(null);
  const [view,setView]=useState<'editor'|'results'>('editor');
  const [modal,setModal]=useState<'presets'|'circuits'|'history'|'replace'|null>(null);
  const [replaceAction,setReplaceAction]=useState<(()=>void)|null>(null);
  const [search,setSearch]=useState('');
  const [busy,setBusy]=useState(false),[connected,setConnected]=useState(false),[initializing,setInitializing]=useState(true);
  const [message,setMessage]=useState<{text:string;error:boolean}|null>(null);
  const messageTimer=useRef<ReturnType<typeof setTimeout>>();
  const started=useRef(false);
  const employeeRef=useRef<HTMLInputElement>(null);
  const displayMessage=useCallback((text:string,error=true)=>{setMessage({text,error});if(messageTimer.current)clearTimeout(messageTimer.current);messageTimer.current=setTimeout(()=>setMessage(null),error?12000:4000);},[]);
  const commit=useCallback((next:CircuitDocument)=>{const previous=docRef.current;if(JSON.stringify(previous)===JSON.stringify(next))return;setPast(items=>[...items.slice(-49),clone(previous)]);setFuture([]);docRef.current=next;setDoc(next);setDirty(true);},[]);
  function loadDocument(document:CircuitDocument,savedCircuit:SavedCircuit|null,nextAnalysis?:Analysis){setDoc(clone(document));setSaved(savedCircuit);setDirty(!savedCircuit);setPast([]);setFuture([]);setSelectedId(null);setSelectedRun(null);setRuns([]);setView('editor');setModal(null);if(nextAnalysis)setAnalysis(clone(nextAnalysis));}
  useEffect(()=>{
    if(started.current)return;started.current=true;
    async function load(){
      const responses=await Promise.allSettled([api<{status:string}>('/health'),api<{components:CatalogComponent[]}>('/catalog'),api<{presets:Preset[]}>('/presets'),api<{circuits:CircuitSummary[]}>('/circuits')]);
      if(responses[0].status==='fulfilled')setConnected(true);
      if(responses[1].status==='fulfilled'&&responses[1].value.components.length)setCatalog(responses[1].value.components);
      if(responses[2].status==='fulfilled'){const options=responses[2].value.presets;setPresets(options);if(options.length){const first=options.find(p=>p.analysis.kind==='transient'&&p.document.components.some(c=>c.kind==='EDD'))??options.find(p=>p.analysis.kind==='transient')??options[0];setDoc(clone(first.document));setAnalysis(clone(first.analysis));setDirty(true);}}
      if(responses[3].status==='fulfilled')setCircuits(responses[3].value.circuits);
      const failure=responses.find(r=>r.status==='rejected');if(failure?.status==='rejected')displayMessage(`サーバーへの接続を確認してください。${errorMessage(failure.reason)}`);
      setInitializing(false);
    }void load();
  },[displayMessage]);
  useEffect(()=>{try{localStorage.setItem('plasma-circuit.employee-id',employee);}catch{/* browser private mode */}},[employee]);
  useEffect(()=>{const listener=(event:BeforeUnloadEvent)=>{if(dirty){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',listener);return()=>window.removeEventListener('beforeunload',listener);},[dirty]);
  const refreshRuns=useCallback(async(id:string)=>{try{const data=await api<{runs:Run[]}>(`/runs?circuit_id=${encodeURIComponent(id)}`);setRuns(data.runs);}catch(e){displayMessage(errorMessage(e));}},[displayMessage]);
  useEffect(()=>{if(saved?.id)void refreshRuns(saved.id);else setRuns([]);},[saved?.id,refreshRuns]);
  useEffect(()=>{
    if(!activeRun||!isBusy(activeRun))return;
    let disposed=false;
    async function poll(){try{const run=await api<Run>(`/runs/${activeRun!.id}`);if(disposed)return;setActiveRun(run);setSelectedRun(previous=>previous?.id===run.id?run:previous);if(!isBusy(run)){void refreshRuns(run.circuit_id);if(run.status==='failed'||run.status==='timed_out')displayMessage(run.error??statusNames[run.status]);}}catch(e){if(!disposed)displayMessage(errorMessage(e));}}
    const timer=setInterval(()=>void poll(),1800);void poll();return()=>{disposed=true;clearInterval(timer);};
  },[activeRun?.id,activeRun?.status,refreshRuns,displayMessage]);
  function undo(){if(!past.length)return;setFuture(items=>[clone(doc),...items]);setDoc(past[past.length-1]);setPast(items=>items.slice(0,-1));setDirty(true);setSelectedId(null);}
  function redo(){if(!future.length)return;setPast(items=>[...items,clone(doc)]);setDoc(future[0]);setFuture(items=>items.slice(1));setDirty(true);setSelectedId(null);}
  useEffect(()=>{function keyboard(event:KeyboardEvent){const target=event.target;if(target instanceof HTMLElement&&(target.isContentEditable||['INPUT','TEXTAREA','SELECT'].includes(target.tagName)))return;if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();if(event.shiftKey)redo();else undo();}if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='y'){event.preventDefault();redo();}}window.addEventListener('keydown',keyboard);return()=>window.removeEventListener('keydown',keyboard);});
  function requireEmployee(){if(!employee.trim()){setEmployeeError(true);employeeRef.current?.focus();displayMessage('保存・計算には社員番号を入力してください。');return false;}setEmployeeError(false);return true;}
  async function saveCurrent():Promise<SavedCircuit|null>{
    if(!requireEmployee())return null;
    const savedDocument=clone(docRef.current);
    const payload=saved?{employee_id:employee.trim(),expected_revision:saved.revision,document:savedDocument}:{employee_id:employee.trim(),document:savedDocument};
    const result=await api<SavedCircuit>(saved?`/circuits/${saved.id}`:'/circuits',{method:saved?'PUT':'POST',body:JSON.stringify(payload)});
    setSaved(result);setDirty(JSON.stringify(docRef.current)!==JSON.stringify(savedDocument));setConnected(true);
    const list=await api<{circuits:CircuitSummary[]}>('/circuits').catch(()=>null);if(list)setCircuits(list.circuits);
    return result;
  }
  function handleError(e:unknown){if(e instanceof ApiError&&e.status===409)displayMessage(`別の人がこの回路を更新しました。編集中の内容は保持しています。「コピーとして保存」で別回路に保存できます。\n${e.message}`);else displayMessage(errorMessage(e));}
  async function save(){if(busy)return;setBusy(true);try{const result=await saveCurrent();if(result)displayMessage(`回路を保存しました（rev.${result.revision}）。`,false);}catch(e){handleError(e);}finally{setBusy(false);}}
  async function run(){
    if(busy||isBusy(activeRun))return;
    if((analysis.kind==='ccp'||analysis.kind==='global')&&(!doc.parameters?.builtin_ccp_template||doc.components.length||doc.wires.length)){displayMessage('プラズマ解析は専用テンプレートで実行します。プリセットからプラズマモデルを開いてください。任意の回路図には通常の回路解析を選択してください。');return;}
    setBusy(true);try{const current=await saveCurrent();if(!current)return;const created=await api<Run>('/runs',{method:'POST',body:JSON.stringify({employee_id:employee.trim(),circuit_id:current.id,expected_revision:current.revision,analysis})});setActiveRun(created);setSelectedRun(created);setView('results');void refreshRuns(current.id);}catch(e){handleError(e);}finally{setBusy(false);}
  }
  async function cancel(){if(!activeRun)return;try{const run=await api<Run>(`/runs/${activeRun.id}/cancel`,{method:'POST'});setActiveRun(run);setSelectedRun(current=>current?.id===run.id?run:current);displayMessage('停止を要求しました。',false);}catch(e){handleError(e);}}
  function replacing(action:()=>void){if(!dirty){action();return;}setReplaceAction(()=>action);setModal('replace');}
  async function openCircuit(id:string){setBusy(true);try{
    const [circuit,history]=await Promise.all([api<SavedCircuit>(`/circuits/${id}`),api<{runs:Run[]}>(`/runs?circuit_id=${encodeURIComponent(id)}`).catch(()=>({runs:[]}))]);
    const kind:AnalysisKind=circuit.document.parameters?.builtin_ccp_template?'ccp':'transient';
    const latest=[...history.runs].sort((a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at))[0];
    const restored=latest?.analysis??{kind,settings:clone(defaultSettings[kind])};
    replacing(()=>loadDocument(circuit.document,circuit,restored));
  }catch(e){handleError(e);}finally{setBusy(false);}}
  async function openRun(id:string){try{const run=await api<Run>(`/runs/${id}`);setSelectedRun(run);setView('results');setModal(null);}catch(e){handleError(e);}}
  function addComponent(entry:CatalogComponent){
    const index=doc.components.filter(c=>c.kind===entry.kind).length+1;
    const component:Component={id:createId(entry.kind.toLowerCase()),kind:entry.kind,label:`${entry.kind}${index}`,ports:clone(entry.ports),parameters:clone(entry.parameters),position:{x:120+(doc.components.length%4)*180,y:100+Math.floor(doc.components.length/4)*140},rotation:['V','I'].includes(entry.kind)?90:0};
    const parameters={...doc.parameters};delete parameters.builtin_ccp_template;
    commit({...doc,components:[...doc.components,component],parameters});setSelectedId(component.id);setView('editor');if(analysis.kind==='ccp'||analysis.kind==='global')setAnalysis({kind:'transient',settings:clone(defaultSettings.transient)});
  }
  function componentChange(component:Component){
    const allowed=new Set(component.ports);
    commit({...doc,components:doc.components.map(c=>c.id===component.id?component:c),wires:doc.wires.filter(w=>!(w.source.component_id===component.id&&!allowed.has(w.source.port))&&!(w.target.component_id===component.id&&!allowed.has(w.target.port)))});
  }
  function deleteComponent(){if(!selectedId)return;commit({...doc,components:doc.components.filter(c=>c.id!==selectedId),wires:doc.wires.filter(w=>w.source.component_id!==selectedId&&w.target.component_id!==selectedId)});setSelectedId(null);}
  function downloadDocument(){const url=URL.createObjectURL(new Blob([JSON.stringify(doc,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`${doc.name||'circuit'}.json`;a.click();URL.revokeObjectURL(url);}
  const selectedComponent=doc.components.find(c=>c.id===selectedId);
  const isPlasma=analysis.kind==='ccp'||analysis.kind==='global';
  const templateMode=isPlasma&&!!doc.parameters.builtin_ccp_template&&!doc.components.length&&!doc.wires.length;
  const grouped=useMemo(()=>{
    const result:Record<string,CatalogComponent[]>={};catalog.filter(c=>`${c.kind} ${c.label} ${c.category}`.toLowerCase().includes(search.toLowerCase())).forEach(c=>{(result[c.category??'その他']??=[]).push(c);});return result;
  },[catalog,search]);
  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><div className="brand-icon"><Zap size={21}/></div><div><strong>Plasma Circuit</strong><span>SIMULATION WORKSPACE</span></div></div>
      <div className="sidebar-projects"><button onClick={()=>setModal('presets')}><BookOpen size={16}/>プリセットから始める<ChevronRight size={14}/></button><button onClick={()=>setModal('circuits')}><FolderOpen size={16}/>保存済みの回路<span className="count">{circuits.length}</span></button></div>
      <div className="sidebar-heading"><span>部品ライブラリ</span><span className="count">{catalog.length}</span></div><div className="library-search"><Search size={14}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="部品を検索" aria-label="部品を検索"/></div>
      <div className="library-list">{Object.entries(grouped).map(([category,entries])=><details key={category} open><summary>{category}<ChevronDown size={12}/></summary><div className="palette-grid">{entries.map(entry=><button className="palette-item" key={entry.kind} onClick={()=>addComponent(entry)} title={`${entry.label}を追加`}><CircuitSymbol kind={entry.kind}/><span>{entry.label}</span><small>{entry.kind}</small></button>)}</div></details>)}{!Object.keys(grouped).length&&<p className="muted text-small">該当する部品はありません。</p>}</div>
      <div className="sidebar-history"><div className="sidebar-heading"><span><History size={14}/>計算履歴</span><button className="text-button" onClick={()=>setModal('history')}>すべて</button></div>{runs.slice(0,3).map(item=><button className={`recent-run ${selectedRun?.id===item.id?'active':''}`} key={item.id} onClick={()=>void openRun(item.id)}><span className={`status-dot ${item.status}`}/><div><strong>{analysisLabel(item.analysis)}</strong><span>rev.{item.circuit_revision} · {relativeTime(item.created_at)}</span></div><ChevronRight size={13}/></button>)}{!runs.length&&<p>保存した回路の計算履歴が<br/>ここに表示されます。</p>}</div>
      <div className="sidebar-footer"><span className={`status-dot ${connected?'green':'amber'}`}/>{initializing?'接続を確認中':connected?'サーバー接続済み':'サーバー未接続'}<span>v0.1</span></div>
    </aside>
    <div className="workbench"><header className="topbar"><div className="workspace-title"><span className="eyebrow">CIRCUIT & PLASMA</span><h1>解析ワークスペース</h1></div><div className="theme-field"><label htmlFor="appearance">外観</label><select id="appearance" value={theme} onChange={e=>setTheme(e.target.value as ThemePreference)}><option value="system">システム</option><option value="light">ライト</option><option value="dark">ダーク</option></select></div><div className="employee-field"><label htmlFor="employee-id">社員番号 <span>必須</span></label><input id="employee-id" ref={employeeRef} className={employeeError?'invalid':''} value={employee} onChange={e=>{setEmployee(e.target.value);setEmployeeError(false);}} placeholder="例：001234" autoComplete="off" spellCheck={false}/></div><div className="topbar-actions"><button className="button secondary" disabled={busy} onClick={()=>void save()}>{busy?<LoaderCircle size={15} className="spin"/>:<Save size={15}/>}保存</button>{isBusy(activeRun)?<button className="button danger-outline" onClick={()=>void cancel()} disabled={activeRun?.cancel_requested}><Square size={14}/>{activeRun?.cancel_requested?'停止要求中':'計算を停止'}</button>:<button className="button primary" disabled={busy||initializing} onClick={()=>void run()}><Play size={15} fill="currentColor"/>計算を実行</button>}</div></header>
      <div className="workspace-bar"><div className="document-title"><CircuitBoard size={18}/><strong title={doc.name}>{doc.name}</strong><span className={`document-state ${dirty?'unsaved':''}`}>{dirty?'未保存':saved?`rev.${saved.revision}`:'新規'}</span></div><div className="workspace-actions"><button className="icon-button" aria-label="元に戻す" title="元に戻す Ctrl+Z" onClick={undo} disabled={!past.length}><Undo2 size={16}/></button><button className="icon-button" aria-label="やり直す" title="やり直す Ctrl+Shift+Z" onClick={redo} disabled={!future.length}><Redo2 size={16}/></button><span className="toolbar-divider"/><button className="icon-button" aria-label="回路JSONをダウンロード" title="回路JSONをダウンロード" onClick={downloadDocument}><Download size={16}/></button><button className="text-button" onClick={()=>replacing(()=>loadDocument(emptyDocument(),null,{kind:'transient',settings:clone(defaultSettings.transient)}))}><Plus size={14}/>新規</button></div></div>
      <div className="workbench-body"><main className="main-workspace"><div className="view-tabs"><button className={view==='editor'?'active':''} onClick={()=>setView('editor')}><CircuitBoard size={16}/>{templateMode?'プラズマモデル':'回路エディタ'}</button><button className={view==='results'?'active':''} onClick={()=>setView('results')}><Activity size={16}/>計算結果{isBusy(activeRun)&&<span className="status-dot running"/>}</button><span className="analysis-badge">{analysisLabel(analysis)}</span></div>
        {view==='editor'?templateMode?<PlasmaDiagram gas={String(analysis.settings.gas??'Ar')} global={analysis.kind==='global'} prescribedPower={isPrescribedPower(analysis)}/>:<Schematic document={doc} selectedId={selectedId} onSelect={setSelectedId} onChange={commit} onMessage={displayMessage}/>:<div className="results-scroll"><Results run={selectedRun} onMessage={displayMessage}/></div>}
      </main><aside className="inspector">{selectedComponent&&view==='editor'&&<ComponentInspector component={selectedComponent} components={doc.components} onChange={componentChange} onDelete={deleteComponent}/>}<AnalysisPanel analysis={analysis} onChange={setAnalysis} document={doc}/><DocumentPanel document={doc} onChange={commit}/><div className="save-info"><Save size={15}/><p>計算前に最新の回路を保存します。<br/>回路・モデル・条件を履歴に記録します。</p></div></aside></div>
      <footer className="workbench-footer"><span><span className="status-dot blue"/>{templateMode?(isPrescribedPower(analysis)?'0D反応モデル 専用テンプレート':'CCP 専用テンプレート'):`${doc.components.length} 部品 · ${doc.wires.length} 配線`}</span><span>PySpice / ngspice · 社員番号は操作履歴に記録</span></footer>
    </div>
    {message&&<div className={`toast ${message.error?'error':'success'}`} role="alert">{message.error?<AlertCircle size={18}/>:<CheckCircle2 size={18}/>}<span>{message.text}</span><button className="icon-button" aria-label="通知を閉じる" onClick={()=>setMessage(null)}><X size={15}/></button></div>}
    {modal&&<div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)setModal(null);}}><section className={`modal ${modal==='replace'?'compact':''}`} role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="modal-header"><h2 id="modal-title">{modal==='presets'?'プリセットから始める':modal==='circuits'?'保存済みの回路':modal==='history'?'計算履歴':'未保存の変更があります'}</h2><button className="icon-button" aria-label="ダイアログを閉じる" onClick={()=>setModal(null)}><X size={19}/></button></div>
      {modal==='replace'?<><p>現在の変更を閉じて、選択した回路を開きます。必要な変更は先に保存してください。</p><div className="modal-actions"><button className="button secondary" onClick={()=>setModal(null)}>編集を続ける</button><button className="button primary" onClick={()=>{replaceAction?.();setModal(null);}}>変更を閉じて開く</button></div></>:modal==='presets'?<><p className="modal-intro">計算できる回路・EDD検証例・CCPモデルを用意しています。</p><div className="preset-grid">{presets.map(preset=><button className="preset-card" key={preset.id} onClick={()=>replacing(()=>loadDocument(preset.document,null,preset.analysis))}><span className="preset-icon">{preset.analysis.kind==='ccp'||preset.analysis.kind==='global'?<Zap size={24}/>:<CircuitBoard size={24}/>}</span><strong>{preset.name}</strong><p>{preset.description}</p><span>{analysisLabel(preset.analysis)}<ArrowRight size={15}/></span></button>)}</div>{!presets.length&&<p className="empty-list">プリセットを取得できていません。APIサーバーの状態を確認してください。</p>}</>:modal==='circuits'?<><div className="modal-intro"><p>社員番号とともに保存された共有回路です。</p>{saved&&<button className="button small secondary" onClick={()=>{setSaved(null);setDirty(true);setModal(null);displayMessage('コピーとして保存できる状態にしました。保存ボタンで別回路を作成します。',false);}}><Copy size={14}/>コピーとして保存</button>}</div><div className="saved-circuits">{circuits.map(circuit=><button key={circuit.id} disabled={busy} onClick={()=>void openCircuit(circuit.id)}><CircuitBoard size={21}/><div><strong>{circuit.name}</strong><span>rev.{circuit.revision} · 更新者 {circuit.updated_by} · {relativeTime(circuit.updated_at)}</span></div><ChevronRight size={18}/></button>)}{!circuits.length&&<p className="empty-list">保存された回路はまだありません。</p>}</div></>:<div className="run-list">{runs.map(item=><button key={item.id} onClick={()=>void openRun(item.id)}><span className={`status-pill ${item.status}`}>{statusNames[item.status]}</span><div><strong>{analysisLabel(item.analysis)}</strong><span>rev.{item.circuit_revision} · {item.employee_id} · {relativeTime(item.created_at)}</span></div><ChevronRight size={18}/></button>)}{!runs.length&&<p className="empty-list">この回路の計算履歴はありません。</p>}</div>}
    </section></div>}
  </div>;
}

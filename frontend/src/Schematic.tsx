import { useCallback, useEffect, useMemo, useState } from 'react';
import { Background, BackgroundVariant, Controls, Handle, Position, ReactFlow, applyNodeChanges, useReactFlow, ReactFlowProvider, useUpdateNodeInternals } from '@xyflow/react';
import type { Connection, EdgeChange, Node, NodeChange, NodeProps } from '@xyflow/react';
import { RotateCw, Trash2, Maximize2, MousePointer2, Cable } from 'lucide-react';
import type { Component, CircuitDocument, Json } from './types';
import { createId, engineering } from './types';

type CircuitNode = Node<{component: Component}, 'component'>;
function Symbol({kind}: {kind:string}) {
  switch (kind) {
    case 'R': return <path d="M0 30H10l5-12 10 24 10-24 10 24 10-24 5 12H80"/>;
    case 'C': return <><path d="M0 30H33M47 30H80M33 10V50M47 10V50"/></>;
    case 'L': return <path d="M0 30h12c0-25 14-25 14 0c0-25 14-25 14 0c0-25 14-25 14 0c0-25 14-25 14 0h12"/>;
    case 'V': return <><path d="M0 30H20M60 30H80"/><circle cx="40" cy="30" r="20"/><path d="M31 23h10m-5-5v10m4 13h10"/></>;
    case 'I': return <><path d="M0 30H20M60 30H80"/><circle cx="40" cy="30" r="20"/><path d="M27 30h26m-8-7 8 7-8 7"/></>;
    case 'D': return <><path d="M0 30H25M55 30H80M25 13l28 17-28 17ZM55 11V49"/></>;
    case 'GND': return <path d="M40 0v24M23 24h34M29 32h22M35 40h10"/>;
    case 'Q': return <><circle cx="40" cy="30" r="24"/><path d="M0 30h28M28 14v32M28 22l29-17h23M28 38l29 17h23m-28-9 5 9-10-1"/></>;
    case 'JUNCTION': return <circle cx="40" cy="30" r="5" fill="currentColor"/>;
    case 'EDD': return <><rect x="17" y="6" width="46" height="48" rx="7"/><path d="M0 30h17M63 30h17"/><text x="40" y="25" textAnchor="middle" className="symbol-text">I(V)</text><text x="40" y="42" textAnchor="middle" className="symbol-text">Q(V)</text></>;
    case 'PLASMA': return <><rect x="16" y="7" width="48" height="46" rx="8"/><path d="M0 30h16m48 0h16M25 18v24M55 18v24M33 21l5 8-4 8 8-5 5 8"/><circle cx="47" cy="22" r="2"/></>;
    case 'S': case 'W': return <><path d="M0 30h24m0 0 30-17m2 17h24"/><circle cx="24" cy="30" r="2"/><circle cx="56" cy="30" r="2"/></>;
    default: return <><path d="M0 30H17M63 30H80"/><rect x="17" y="8" width="46" height="44" rx="5"/><text x="40" y="36" textAnchor="middle" className="symbol-text">{kind}</text></>;
  }
}
export function CircuitSymbol({kind}: {kind: string}) { return <svg viewBox="0 0 80 60" className="palette-symbol" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round"><Symbol kind={kind}/></svg>; }
function ComponentNode({data, selected}: NodeProps<CircuitNode>) {
  const c = data.component;
  const updateInternals=useUpdateNodeInternals();
  useEffect(()=>updateInternals(c.id),[c.id,c.ports.join('|'),c.rotation,updateInternals]);
  const valueUnit = ({R:'Ω', C:'F', L:'H', V:'V', I:'A'} as Record<string,string>)[c.kind];
  const waveform=c.parameters.waveform && typeof c.parameters.waveform==='object' && !Array.isArray(c.parameters.waveform) ? c.parameters.waveform as Record<string,Json> : null;
  const sourceCaption=waveform?.kind==='sin'?`${engineering(Number(waveform.amplitude??0),valueUnit)}pk · ${engineering(Number(waveform.frequency??0),'Hz')}`:waveform?.kind==='pulse'?'PULSE':waveform?.kind==='pwl'?'PWL':typeof c.parameters.dc==='number'?engineering(c.parameters.dc,valueUnit):'';
  const detailedDiode=c.kind==='D'&&'model_parameters' in c.parameters;
  const caption = detailedDiode ? '詳細設定' : typeof c.parameters.value === 'number' ? engineering(c.parameters.value,valueUnit) : ['V','I'].includes(c.kind) ? sourceCaption : c.kind==='PLASMA'?`${c.parameters.gas??'Ar'} · nₑ ${engineering(Number(c.parameters.electron_density_m3??1e16))}` : (c.kind === 'EDD' ? `${Array.isArray(c.parameters.branches) ? c.parameters.branches.length : 1} 枝` : String(c.parameters.model ?? ''));
  const rotation = ((c.rotation % 360) + 360) % 360;
  return <div className={`circuit-node ${selected ? 'selected' : ''} ${c.kind==='JUNCTION'?'junction':''}`}>
    <div className="node-body" style={{transform:`rotate(${rotation}deg)`}}>
      <svg viewBox="0 0 80 60" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round"><Symbol kind={c.kind}/></svg>
    </div>
    {c.ports.map((port,index) => {
      let side = c.kind === 'GND' ? Position.Top : index === 0 ? Position.Left : Position.Right;
      let offset = c.ports.length > 2 && index > 0 ? 20 + (index - 1) / Math.max(1, c.ports.length - 2) * 40 : 30;
      let x = side === Position.Left ? 0 : side === Position.Right ? 80 : 40;
      let y = side === Position.Top ? 0 : offset;
      if(c.kind==='Q'){x=port==='b'?0:80;y=port==='c'?5:port==='e'?55:30;side=port==='b'?Position.Left:Position.Right;}
      if (c.kind === 'JUNCTION') {x=40;y=30;}
      const radians = rotation * Math.PI/180;
      const px = 40 + (x-40)*Math.cos(radians) - (y-30)*Math.sin(radians);
      const py = 30 + (x-40)*Math.sin(radians) + (y-30)*Math.cos(radians);
      const sides = [Position.Right, Position.Bottom, Position.Left, Position.Top];
      if (rotation) side = sides[(sides.indexOf(side)+rotation/90)%4] ?? side;
      return <div key={port}>
        <Handle id={port} type="source" position={side} isConnectable style={{left:px,top:py,transform:'translate(-50%, -50%)'}} aria-label={`${c.label} 端子 ${port}`}/>
        {c.kind!=='JUNCTION' && <span className="port-label" style={{left:px,top:py}}>{port}</span>}
      </div>;
    })}
    {c.kind!=='JUNCTION' && <><div className="node-label">{c.label}</div><div className="node-value">{caption}</div></>}
  </div>;
}
const nodeTypes = {component: ComponentNode};

interface Props {document:CircuitDocument; selectedId:string|null; onSelect:(id:string|null)=>void; onChange:(doc:CircuitDocument)=>void; onMessage:(message:string)=>void}
function Editor(props:Props) {
  const {document:doc,onChange,onSelect,selectedId} = props;
  const flow = useReactFlow();
  const mapped = useMemo<CircuitNode[]>(() => doc.components.map(c => ({id:c.id,type:'component',position:c.position,data:{component:c},selected:c.id===selectedId})), [doc, selectedId]);
  const [nodes,setNodes] = useState<CircuitNode[]>(mapped);
  useEffect(()=>setNodes(mapped),[mapped]);
  const edges = useMemo(()=>doc.wires.map(w=>({id:w.id,source:w.source.component_id,target:w.target.component_id,sourceHandle:w.source.port,targetHandle:w.target.port,type:'step',style:{stroke:'var(--text-muted)',strokeWidth:2},interactionWidth:20})),[doc.wires]);
  const onNodesChange = useCallback((changes:NodeChange<CircuitNode>[])=>{
    const deleted = new Set(changes.filter(c=>c.type==='remove').map(c=>c.id));
    if (deleted.size) {onChange({...doc,components:doc.components.filter(c=>!deleted.has(c.id)),wires:doc.wires.filter(w=>!deleted.has(w.source.component_id)&&!deleted.has(w.target.component_id))});onSelect(null);}
    else setNodes(previous=>applyNodeChanges(changes,previous));
  },[doc,onChange,onSelect]);
  const onEdgesChange = (changes:EdgeChange[])=>{
    const deleted = new Set(changes.filter(c=>c.type==='remove').map(c=>c.id));
    if (deleted.size) onChange({...doc,wires:doc.wires.filter(w=>!deleted.has(w.id))});
  };
  function connect(connection:Connection) {
    if (!connection.sourceHandle || !connection.targetHandle) return;
    if (connection.source===connection.target && connection.sourceHandle===connection.targetHandle) return;
    const source={component_id:connection.source,port:connection.sourceHandle}, target={component_id:connection.target,port:connection.targetHandle};
    const existing=doc.wires.some(w=>(JSON.stringify(w.source)===JSON.stringify(source)&&JSON.stringify(w.target)===JSON.stringify(target))||(JSON.stringify(w.target)===JSON.stringify(source)&&JSON.stringify(w.source)===JSON.stringify(target)));
    if (existing) {props.onMessage('この端子間は接続済みです。');return;}
    onChange({...doc,wires:[...doc.wires,{id:createId('w'),source,target}]});
  }
  function selectedAction(action:'rotate'|'delete') {
    if (!selectedId) return;
    if (action==='delete') {onChange({...doc,components:doc.components.filter(c=>c.id!==selectedId),wires:doc.wires.filter(w=>w.source.component_id!==selectedId&&w.target.component_id!==selectedId)});onSelect(null);}
    else onChange({...doc,components:doc.components.map(c=>c.id===selectedId?{...c,rotation:(c.rotation+90)%360}:c)});
  }
  return <div className="schematic-wrap">
    <ReactFlow<CircuitNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onConnect={connect} connectionLineStyle={{stroke:'var(--accent)',strokeWidth:2}} connectionMode={'loose' as import('@xyflow/react').ConnectionMode} onNodeClick={(_,node)=>onSelect(node.id)} onPaneClick={()=>onSelect(null)}
      onNodeDragStop={(_,node)=>onChange({...doc,components:doc.components.map(c=>c.id===node.id?{...c,position:node.position}:c)})}
      snapToGrid snapGrid={[20,20]} fitView fitViewOptions={{padding:0.35}} minZoom={0.2} maxZoom={2} deleteKeyCode={['Backspace','Delete']} proOptions={{hideAttribution:true}}>
      <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} color="var(--border)"/>
      <Controls showInteractive={false}/>
    </ReactFlow>
    <div className="canvas-toolbar"><span><MousePointer2 size={14}/>選択</span><span><Cable size={14}/>端子から配線</span><div className="toolbar-divider"/><button title="90度回転" aria-label="選択部品を90度回転" disabled={!selectedId} onClick={()=>selectedAction('rotate')}><RotateCw size={16}/></button><button title="削除" aria-label="選択部品を削除" disabled={!selectedId} onClick={()=>selectedAction('delete')}><Trash2 size={16}/></button><button title="全体を表示" aria-label="回路全体を表示" onClick={()=>flow.fitView({padding:0.35,duration:250})}><Maximize2 size={16}/></button></div>
    {doc.components.length===0 && <div className="canvas-empty"><CircuitSymbol kind="R"/><h3>回路を組み立てる</h3><p>左のライブラリから部品を追加し、<br/>端子の丸印をドラッグして接続します。</p></div>}
    <div className="canvas-note">線の交差は接続されません。分岐には接続点を使います。</div>
  </div>;
}
export default function Schematic(props:Props) {return <ReactFlowProvider><Editor {...props}/></ReactFlowProvider>;}

export function PlasmaDiagram({gas, global, prescribedPower=false,kind='ccp'}:{gas:string;global:boolean;prescribedPower?:boolean;kind?:string}) {
  if(prescribedPower)return <div className="plasma-diagram">
    <div className="diagram-eyebrow">PRESCRIBED POWER · {kind==='global_transient'?'TIME-DEPENDENT':'STEADY'} 0D</div>
    <h2>{gas} 0D反応モデル</h2><p>反応・壁損失・粒子エネルギー収支{kind==='global_transient'&&'・ガス温度の時間発展'}</p>
    <svg viewBox="0 0 660 260" role="img" aria-label={`${gas}の粒子収支・電子エネルギー収支${kind==='global_transient'?'・ガス温度を時間積分する':'から定常状態を求める'}指定総吸収プラズマ電力0Dモデルの概念図`}>
      <rect x="20" y="70" width="145" height="90" rx="9" fill="var(--bg)" stroke="var(--border)"/><text x="92" y="105" textAnchor="middle">総吸収プラズマ電力</text><text x="92" y="133" textAnchor="middle" className="diagram-title">Pabs [W]</text>
      <path d="M171 115h56m-7-6 7 6-7 6" stroke="var(--text-muted)" strokeWidth="2" fill="none"/>
      <rect x="235" y="48" width="198" height="133" rx="9" fill="var(--surface-2)" stroke="var(--border)"/><text x="334" y="80" textAnchor="middle" className="diagram-title">{gas} REACTION MODEL</text><text x="334" y="108" textAnchor="middle">粒子収支</text><text x="334" y="137" textAnchor="middle">電子エネルギー収支</text><text x="334" y="161" textAnchor="middle" className="diagram-small">{kind==='global_transient'?'マクロ時間を積分':'定常状態まで収束'}</text>
      <path d="M440 115h48m-7-6 7 6-7 6" stroke="var(--text-muted)" strokeWidth="2" fill="none"/>
      <rect x="495" y="70" width="145" height="90" rx="9" fill="var(--bg)" stroke="var(--border)"/><text x="568" y="105" textAnchor="middle">各粒子の密度</text><text x="568" y="133" textAnchor="middle">電子温度 Tₑ</text>
      <rect x="238" y="210" width="192" height="38" rx="8" fill="var(--bg)" stroke="var(--border)"/><text x="334" y="234" textAnchor="middle">反応データ・壁損失</text><path d="M334 205v-17m-5 6 5-6 5 6" stroke="var(--text-muted)" strokeWidth="1.5" fill="none"/>
    </svg>
    <div className="model-notice"><strong>総吸収プラズマ電力による0D反応計算</strong><p>入力した総吸収プラズマ電力から粒子密度・電子温度を求めます。材料別の係数・表面状態と出典を確認してください。</p></div>
    <div className="model-status"><span className="status-dot amber"/>文献反応モデル・装置への適用確認は未完了</div>
  </div>;
  return <div className="plasma-diagram">
    <div className="diagram-eyebrow">BUILT-IN CCP MODEL</div>
    <h2>{gas} {kind==='radial'?'径方向・分布回路':'容量結合プラズマ'}</h2><p>非対称電極・2シース・バルクの等価モデル</p>
    <svg viewBox="0 0 660 260" role="img" aria-label="RF電源、駆動側シース、プラズマバルク、接地側シースからなるCCPモデルの概念図">
      <defs><linearGradient id="plasmaFill"><stop stopColor="var(--surface)"/><stop offset="1" stopColor="var(--surface-2)"/></linearGradient></defs>
      <path d="M70 128V65H208M465 65H575V196H70v-38" stroke="var(--text-muted)" strokeWidth="2" fill="none"/>
      <circle cx="70" cy="143" r="26" stroke="var(--text)" strokeWidth="2" fill="var(--bg)"/>
      <path d="M53 143q8-17 17 0t17 0" fill="none" stroke="var(--text)" strokeWidth="2"/>
      <text x="70" y="239" textAnchor="middle">RF 駆動</text>
      <rect x="209" y="24" width="42" height="112" rx="6" fill="var(--surface)" stroke="var(--border)"/><rect x="421" y="24" width="42" height="112" rx="6" fill="var(--surface)" stroke="var(--border)"/>
      <rect x="257" y="24" width="158" height="112" rx="6" fill="url(#plasmaFill)" stroke="var(--border)"/>
      <path d="M201 20V140M470 20V140" stroke="var(--text)" strokeWidth="5"/>
      <text x="337" y="69" textAnchor="middle" className="diagram-title">PLASMA BULK</text><text x="337" y="98" textAnchor="middle">nₑ · Tₑ · νₘ</text>
      <text x="230" y="163" textAnchor="middle">シース 1</text><text x="442" y="163" textAnchor="middle">シース 2</text>
      <text x="180" y="12" textAnchor="middle" className="diagram-small">駆動電極</text><text x="492" y="12" textAnchor="middle" className="diagram-small">接地電極</text>
      <path d="M575 196v17m-14 0h28m-20 7h12m-8 7h4" stroke="var(--text-muted)" strokeWidth="2" fill="none"/>
      {global && <><rect x="245" y="207" width="185" height="40" rx="8" fill="var(--bg)" stroke="var(--border)"/><text x="337" y="231" textAnchor="middle">粒子・電子エネルギー収支</text><path d="M313 136v65m-5-6 5 6 5-6M361 201v-59m-5 6 5-6 5 6" stroke="var(--text-muted)" strokeWidth="1.5" fill="none"/></>}
    </svg>
    <div className="model-notice"><strong>専用テンプレートによる計算</strong><p>右の条件から2シース・バルクの等価回路を生成します。外部RLC・2周波数駆動を詳細条件で設定できます。回路図でPLASMA素子に接続して計算することもできます。</p></div>
    <div className="model-status"><span className="status-dot amber"/>研究用モデル・実験値との妥当性検証は未完了</div>
  </div>;
}

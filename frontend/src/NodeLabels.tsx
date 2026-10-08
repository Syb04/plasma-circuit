import {useEffect,useMemo,useRef,useState} from 'react';
import {Tag} from 'lucide-react';
import type {CircuitDocument,Component,Endpoint} from './types';

export function endpointKey(endpoint:Endpoint){return `${endpoint.component_id}.${endpoint.port}`;}
export function endpointGroups(doc:CircuitDocument):Map<string,string>{
  const parents=new Map<string,string>();
  doc.components.forEach(c=>c.ports.forEach(port=>{const key=endpointKey({component_id:c.id,port});parents.set(key,key);}));
  function find(key:string):string{let root=key;while(parents.get(root)!==root)root=parents.get(root)!;while(key!==root){const next=parents.get(key)!;parents.set(key,root);key=next;}return root;}
  function union(a:string,b:string){if(parents.has(a)&&parents.has(b))parents.set(find(b),find(a));}
  doc.wires.forEach(w=>union(endpointKey(w.source),endpointKey(w.target)));
  const grounds:string[]=[];
  doc.components.forEach(c=>{const kind=c.kind.toUpperCase();if(kind==='COAX')union(`${c.id}.n1`,`${c.id}.n2`);if(kind==='GND')c.ports.forEach(port=>grounds.push(`${c.id}.${port}`));});
  grounds.slice(1).forEach(key=>union(grounds[0],key));
  return new Map([...parents.keys()].map(key=>[key,find(key)]));
}
export function nodeNames(doc:CircuitDocument,endpoint:Endpoint,groups=endpointGroups(doc)):string[]{
  const group=groups.get(endpointKey(endpoint));
  if(group===undefined)return [];
  return [...new Set((doc.node_labels??[]).filter(label=>groups.get(endpointKey(label))===group).map(label=>label.name))];
}
export function endpointNames(doc:CircuitDocument):Map<string,string>{
  const groups=endpointGroups(doc),labels=new Map<string,string>();
  (doc.node_labels??[]).forEach(label=>{const group=groups.get(endpointKey(label));if(group!==undefined&&!labels.has(group))labels.set(group,label.name);});
  return new Map([...groups].flatMap(([endpoint,group])=>labels.has(group)?[[endpoint,labels.get(group)!]]:[]));
}
function withNodeLabel(doc:CircuitDocument,endpoint:Endpoint,value:string):CircuitDocument{
  const name=value.trim(),groups=endpointGroups(doc),group=groups.get(endpointKey(endpoint));
  if(group===undefined)throw new Error('ノード名を付ける端子が存在しません。');
  if(Array.from(name).length>100||/[\p{Cc}\p{Zl}\p{Zp}]/u.test(name))throw new Error('ノード名は制御文字・改行を含まない100文字以内で指定してください。');
  const remaining=(doc.node_labels??[]).filter(label=>groups.get(endpointKey(label))!==group);
  if(name&&remaining.some(label=>label.name===name))throw new Error('別のノードで使われている名前です。別の名前を指定してください。');
  if(name&&remaining.length>=2000)throw new Error('1つの回路に付けられるノード名は2000個までです。');
  return {...doc,node_labels:name?[...remaining,{...endpoint,name}]:remaining};
}
export function pruneNodeLabels(doc:CircuitDocument):CircuitDocument{
  if(!doc.node_labels)return doc;
  const endpoints=new Set(doc.components.flatMap(c=>c.ports.map(port=>`${c.id}.${port}`)));
  return {...doc,node_labels:doc.node_labels.filter(label=>endpoints.has(endpointKey(label)))};
}

export function NodeLabelEditor({document:doc,component,selectedPort,onSelectPort,onChange}:{document:CircuitDocument;component:Component;selectedPort:string|null;onSelectPort:(endpoint:Endpoint)=>void;onChange:(doc:CircuitDocument)=>void}){
  const port=component.ports.includes(selectedPort??'')?selectedPort!:component.ports[0];
  const endpoint={component_id:component.id,port};
  const groups=useMemo(()=>endpointGroups(doc),[doc]);
  const names=nodeNames(doc,endpoint,groups),current=names[0]??'';
  const [text,setText]=useState(current),[error,setError]=useState('');
  const input=useRef<HTMLInputElement>(null);
  useEffect(()=>{setText(current);setError('');},[component.id,port,current]);
  useEffect(()=>{if(selectedPort!==null)input.current?.focus();},[component.id,selectedPort]);
  if(!port)return null;
  const count=[...groups.values()].filter(group=>group===groups.get(endpointKey(endpoint))).length;
  function apply(value:string){try{onChange(withNodeLabel(doc,endpoint,value));setError('');}catch(reason){setError(reason instanceof Error?reason.message:String(reason));}}
  return <section className="node-label-editor" aria-label="ノード名の設定"><h4><Tag size={13}/>端子・ノード名</h4>
    <label>対象の端子<select aria-label="ノードの端子" value={port} onChange={e=>onSelectPort({component_id:component.id,port:e.target.value})}>{component.ports.map(p=><option key={p} value={p}>{p}{nodeNames(doc,{component_id:component.id,port:p},groups)[0]?` — ${nodeNames(doc,{component_id:component.id,port:p},groups)[0]}`:''}</option>)}</select></label>
    <label>ノード名<input ref={input} aria-label="ノード名" value={text} placeholder="例: RF入力、電極、Vout" onChange={e=>{setText(e.target.value);setError('');}} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();apply(text);}}}/></label>
    <div className="flex-row"><button className="button small secondary" disabled={text.trim()===current&&names.length<2} onClick={()=>apply(text)}>ノード名を適用</button><button className="text-button" disabled={!names.length} onClick={()=>apply('')}>ノード名を解除</button></div>
    {error&&<p className="field-error" role="alert">{error}</p>}{names.length>1&&<p className="field-error">同じノードに複数の名前があります。名前を適用して統一してください。</p>}
    <p className="muted text-small">{count}端子が同じノードです。配線や端子名をクリックして選べます。適用した名前で、対GND電圧の波形を V(名前) と表示します。</p>
  </section>;
}

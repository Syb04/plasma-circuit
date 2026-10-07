import {useEffect,useState} from 'react';
import {Check} from 'lucide-react';

export function JsonEditor({value,onApply,label='JSON',rows=8}:{value:unknown;onApply:(value:unknown)=>void;label?:string;rows?:number}) {
  const [text,setText]=useState(JSON.stringify(value,null,2));
  const [error,setError]=useState('');
  useEffect(()=>{setText(JSON.stringify(value,null,2));setError('');},[value]);
  const dirty=text!==JSON.stringify(value,null,2);
  function apply() {try {onApply(JSON.parse(text));setError('');} catch(e) {setError(e instanceof Error?e.message:String(e));}}
  return <div className="json-editor"><label>{label}</label><textarea spellCheck={false} rows={rows} value={text} onChange={e=>setText(e.target.value)} aria-label={label}/>{error&&<p className="field-error">{error}</p>}<button className="button small secondary" disabled={!dirty} onClick={apply}><Check size={13}/>適用</button></div>;
}

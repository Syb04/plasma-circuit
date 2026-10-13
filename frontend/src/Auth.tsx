import {useCallback,useEffect,useRef,useState} from 'react';
import type {FormEvent,ReactNode} from 'react';
import {KeyRound,LoaderCircle,LogIn,Zap} from 'lucide-react';
import {api,ApiError,setCsrfToken} from './types';
import type {Account,AuthSession} from './types';
import {useTheme} from './theme';
import type {ThemePreference} from './theme';
import {AccountDialog} from './Accounts';

function AuthFrame({children}:{children:ReactNode}){
  const [theme,setTheme]=useTheme();
  return <main className="auth-page"><div className="auth-appearance"><label htmlFor="auth-theme">外観</label><select id="auth-theme" value={theme} onChange={e=>setTheme(e.target.value as ThemePreference)}><option value="system">システム</option><option value="light">ライト</option><option value="dark">ダーク</option></select></div><section className="auth-card"><div className="auth-brand"><div className="brand-icon"><Zap size={24}/></div><div><strong>Plasma Circuit</strong><span>SIMULATION WORKSPACE</span></div></div>{children}</section></main>;
}

function Login({initialized,onLogin}:{initialized:boolean;onLogin:(session:AuthSession,password:string)=>void}){
  const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function submit(event:FormEvent){event.preventDefault();if(busy)return;setBusy(true);setError('');try{const next=await api<AuthSession>('/auth/login',{method:'POST',body:JSON.stringify({username:username.trim(),password})});onLogin(next,password);setPassword('');}catch(reason){setError(reason instanceof Error?reason.message:String(reason));}finally{setBusy(false);}}
  return <AuthFrame><h1>ログイン</h1><p className="muted">自分のモデルと計算結果を開きます。</p>{!initialized&&<p className="warning-box">初期管理者がまだ登録されていません。サーバーの初期設定を行ってください。</p>}<form onSubmit={e=>void submit(e)}><label htmlFor="login-id">ログインID（社員番号）<input id="login-id" autoComplete="username" required maxLength={80} value={username} onChange={e=>setUsername(e.target.value)} autoFocus/></label><label htmlFor="login-password">パスワード<input id="login-password" type="password" autoComplete="current-password" required maxLength={128} value={password} onChange={e=>setPassword(e.target.value)}/></label>{error&&<p className="field-error" role="alert">{error}</p>}<button className="button primary" disabled={busy||!initialized}>{busy?<LoaderCircle size={16} className="spin"/>:<LogIn size={16}/>}ログイン</button></form><p className="auth-help">アカウントの発行やパスワードの再発行は管理者に依頼してください。</p></AuthFrame>;
}

function PasswordForm({initialCurrent='',required,onComplete,onCancel}:{initialCurrent?:string;required:boolean;onComplete:(session:AuthSession)=>void;onCancel:()=>void}){
  const [current,setCurrent]=useState(initialCurrent),[password,setPassword]=useState(''),[confirmation,setConfirmation]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function submit(event:FormEvent){event.preventDefault();if(busy)return;if(password!==confirmation){setError('新しいパスワードが一致しません。');return;}setBusy(true);setError('');try{onComplete(await api<AuthSession>('/auth/password',{method:'POST',body:JSON.stringify({current_password:current,new_password:password})}));setCurrent('');setPassword('');setConfirmation('');}catch(reason){setError(reason instanceof Error?reason.message:String(reason));}finally{setBusy(false);}}
  return <><h1>{required?'仮パスワードを変更':'パスワードを変更'}</h1><p className="muted">{required?'利用を始める前に、自分のパスワードを設定してください。':'変更後は、この画面以外のログインが解除されます。'}</p><form onSubmit={e=>void submit(e)}><label htmlFor="current-password">現在のパスワード<input id="current-password" type="password" autoComplete="current-password" required maxLength={128} value={current} onChange={e=>setCurrent(e.target.value)} autoFocus/></label><label htmlFor="new-password">新しいパスワード（12〜128文字）<input id="new-password" type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={password} onChange={e=>setPassword(e.target.value)}/></label><label htmlFor="confirm-password">新しいパスワード（確認）<input id="confirm-password" type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={confirmation} onChange={e=>setConfirmation(e.target.value)}/></label>{error&&<p className="field-error" role="alert">{error}</p>}<div className="flex-row"><button className="button primary" disabled={busy}>{busy?<LoaderCircle size={16} className="spin"/>:<KeyRound size={16}/>}パスワードを保存</button><button type="button" className="button secondary" disabled={busy} onClick={onCancel}>{required?'ログアウト':'キャンセル'}</button></div></form></>;
}

export default function AuthGate({children}:{children:(user:Account,onLogout:()=>Promise<void>,onPassword:()=>void)=>ReactNode}){
  const [account,setAccount]=useState<Account|null>(null),[loading,setLoading]=useState(true),[initialized,setInitialized]=useState(true),[error,setError]=useState(''),[passwordOpen,setPasswordOpen]=useState(false),[initialPassword,setInitialPassword]=useState('');
  const generation=useRef(0);
  const accept=useCallback((session:AuthSession)=>{generation.current++;setCsrfToken(session.csrf_token);setAccount(session.account);setError('');setLoading(false);},[]);
  const refresh=useCallback(async()=>{const version=generation.current;try{const session=await api<AuthSession>('/auth/me');if(version===generation.current){setCsrfToken(session.csrf_token);setAccount(session.account);setError('');}}catch(reason){if(version!==generation.current)return;if(reason instanceof ApiError&&reason.status===401){setCsrfToken('');setAccount(null);setPasswordOpen(false);setInitialPassword('');}else setError(reason instanceof Error?reason.message:String(reason));}finally{if(version===generation.current)setLoading(false);}},[]);
  useEffect(()=>{void api<{initialized:boolean}>('/auth/status').then(status=>setInitialized(status.initialized)).catch(()=>{});void refresh();const check=()=>{if(document.visibilityState==='visible')void refresh();};window.addEventListener('plasma-auth-required',check);window.addEventListener('focus',check);document.addEventListener('visibilitychange',check);const timer=setInterval(check,30000);return()=>{window.removeEventListener('plasma-auth-required',check);window.removeEventListener('focus',check);document.removeEventListener('visibilitychange',check);clearInterval(timer);};},[refresh]);
  async function logout(){try{await api('/auth/logout',{method:'POST'});}catch(reason){if(!(reason instanceof ApiError&&reason.status===401))throw reason;}generation.current++;setCsrfToken('');setAccount(null);setPasswordOpen(false);setInitialPassword('');setError('');}
  if(loading)return <AuthFrame><p role="status"><LoaderCircle size={18} className="spin"/> ログインを確認しています…</p></AuthFrame>;
  if(!account)return <>{error&&<div className="auth-connection-error" role="alert">{error}<button className="text-button" onClick={()=>void refresh()}>再試行</button></div>}<Login initialized={initialized} onLogin={(session,password)=>{accept(session);setInitialPassword(session.account.must_change_password?password:'');}}/></>;
  function completePassword(session:AuthSession){accept(session);setInitialPassword('');setPasswordOpen(false);}
  if(account.must_change_password)return <AuthFrame><PasswordForm required initialCurrent={initialPassword} onComplete={completePassword} onCancel={()=>void logout().catch(reason=>setError(String(reason)))}/>{error&&<p role="alert" className="field-error">{error}</p>}</AuthFrame>;
  return <>{children(account,logout,()=>setPasswordOpen(true))}{passwordOpen&&<AccountDialog title="パスワード変更" onClose={()=>setPasswordOpen(false)}><PasswordForm required={false} onComplete={completePassword} onCancel={()=>setPasswordOpen(false)}/></AccountDialog>}</>;
}

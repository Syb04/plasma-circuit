import {useEffect, useRef, useState} from 'react';
import {ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, CircuitBoard, Copy, Filter, FolderOpen, LoaderCircle, RefreshCw, Search, Trash2, X} from 'lucide-react';
import {api, ApiError} from './types';
import type {CircuitList} from './types';

export interface ModelFilters {
  query: string;
  updatedBy: string;
  updatedWithin: string;
  sort: string;
  page: number;
  pageSize: number;
}
export const defaultModelFilters: ModelFilters = {
  query: '', updatedBy: '', updatedWithin: '', sort: 'updated_desc', page: 1, pageSize: 25,
};
function dateLabel(value: string) {
  return new Date(value).toLocaleString('ja-JP', {year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}

export default function ModelLibrary({filters, onFiltersChange, employee, currentId, currentName, dirty, busy, onOpen, onBack, onCopy, onTotal, onDeleted}: {
  filters: ModelFilters;
  onFiltersChange: (next: ModelFilters) => void;
  employee: string;
  currentId?: string;
  currentName: string;
  dirty: boolean;
  busy: boolean;
  onOpen: (id: string) => void;
  onBack: () => void;
  onCopy: () => void;
  onTotal: (total: number) => void;
  onDeleted: (ids: string[]) => void;
}) {
  const [data, setData] = useState<CircuitList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmation, setConfirmation] = useState<CircuitList['circuits'] | null>(null);
  const [deleting, setDeleting] = useState(false);
  const deletingRef = useRef(false); deletingRef.current = deleting;
  const [actionError, setActionError] = useState('');
  const selectAllRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setSelected([]);
    const timer = setTimeout(() => {
      const params = new URLSearchParams({limit: String(filters.pageSize), offset: String((filters.page - 1) * filters.pageSize), sort: filters.sort});
      if (filters.query.trim()) params.set('q', filters.query.trim());
      if (filters.updatedBy.trim()) params.set('updated_by', filters.updatedBy.trim());
      if (filters.updatedWithin) params.set('updated_within_days', filters.updatedWithin);
      void api<CircuitList>(`/circuits?${params}`, {signal: controller.signal}).then(result => {
        if (controller.signal.aborted) return;
        onTotal(result.total_all);
        if (filters.page > 1 && result.offset >= result.total) {
          onFiltersChange({...filters, page: 1});
          return;
        }
        setData(result);
        setLoading(false);
      }).catch(reason => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : String(reason));
        setLoading(false);
      });
    }, 250);
    return () => {clearTimeout(timer); controller.abort();};
  }, [filters, refresh, onTotal, onFiltersChange]);

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = selected.length > 0 && selected.length < (data?.circuits.length ?? 0);
  }, [selected, data, loading]);
  useEffect(() => {
    if (!confirmation) return;
    const previousFocus = document.activeElement;
    cancelRef.current?.focus();
    function keyboard(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (!deletingRef.current) setConfirmation(null);
      }
      if (event.key !== 'Tab') return;
      const buttons = Array.from(dialogRef.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
      if (!buttons.length) {event.preventDefault(); return;}
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first.focus();}
      else if (!buttons.includes(document.activeElement as HTMLButtonElement)) {event.preventDefault(); first.focus();}
    }
    document.addEventListener('keydown', keyboard);
    return () => {document.removeEventListener('keydown', keyboard); if (previousFocus instanceof HTMLElement) previousFocus.focus();};
  }, [confirmation]);

  function changeFilters(next: ModelFilters) {setSelected([]); setActionError(''); onFiltersChange(next);}
  function filter(change: Partial<ModelFilters>) {changeFilters({...filters, ...change, page: 1});}
  async function deleteSelected() {
    if (!confirmation || deletingRef.current || !employee.trim()) return;
    deletingRef.current = true;
    setDeleting(true);
    setActionError('');
    try {
      const result = await api<{deleted_ids: string[]}>('/circuits/delete', {method: 'POST', body: JSON.stringify({employee_id: employee.trim(), circuits: confirmation.map(model => ({id: model.id, expected_revision: model.revision}))})});
      setConfirmation(null);
      setSelected([]);
      setLoading(true);
      onDeleted(result.deleted_ids);
      setRefresh(n => n + 1);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
      if (reason instanceof ApiError && [404, 409].includes(reason.status)) {
        setConfirmation(null);
        setSelected([]);
        setLoading(true);
        setRefresh(n => n + 1);
      }
    } finally {deletingRef.current = false; setDeleting(false);}
  }
  const hasFilters = !!(filters.query || filters.updatedBy || filters.updatedWithin);
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / filters.pageSize));
  const unavailable = loading || busy || deleting;

  return <main className="model-library" aria-labelledby="model-library-title">
    <div className="model-library-heading">
      <div><span className="eyebrow">MODEL LIBRARY</span><h2 id="model-library-title">保存済みモデル</h2><p>共有された回路・プラズマモデルを検索して開けます。</p></div>
      <button className="button secondary" onClick={onBack} disabled={busy || deleting}><ArrowLeft size={15}/>編集画面に戻る</button>
    </div>
    <div className="current-model">
      <CircuitBoard size={18}/><div><span>編集中のモデル</span><strong>{currentName || '名称未設定'}</strong></div>
      {dirty && <span className="document-state unsaved">未保存</span>}
      {currentId && <button className="button small secondary" onClick={onCopy} disabled={busy || deleting}><Copy size={14}/>コピーとして保存</button>}
    </div>
    <section className="model-library-filters" aria-label="モデルの検索条件">
      <label className="model-query" htmlFor="model-query">モデルを検索<div className="model-search-input"><Search size={17}/><input id="model-query" aria-label="モデルを検索" type="search" placeholder="モデル名・メモ・社員番号" maxLength={200} disabled={deleting} value={filters.query} onChange={e => filter({query: e.target.value})}/>{filters.query && <button className="icon-button" aria-label="モデル検索をクリア" disabled={deleting} onClick={() => filter({query: ''})}><X size={15}/></button>}</div></label>
      <label htmlFor="model-updated-by">更新者（社員番号）<input id="model-updated-by" aria-label="更新者（社員番号）" value={filters.updatedBy} maxLength={80} placeholder="すべての更新者" disabled={deleting} onChange={e => filter({updatedBy: e.target.value})}/></label>
      <label htmlFor="model-updated-within">更新期間<select id="model-updated-within" aria-label="更新期間" value={filters.updatedWithin} disabled={deleting} onChange={e => filter({updatedWithin: e.target.value})}><option value="">全期間</option><option value="7">過去7日</option><option value="30">過去30日</option><option value="90">過去90日</option></select></label>
      <label htmlFor="model-sort">並べ替え<select id="model-sort" aria-label="並べ替え" value={filters.sort} disabled={deleting} onChange={e => filter({sort: e.target.value})}><option value="updated_desc">更新が新しい順</option><option value="updated_asc">更新が古い順</option><option value="name_asc">モデル名の昇順</option><option value="name_desc">モデル名の降順</option><option value="created_desc">作成が新しい順</option></select></label>
      <div className="model-filter-actions"><button className="button small secondary" disabled={!employee.trim() || deleting} onClick={() => filter({updatedBy: employee.trim()})}><Filter size={13}/>自分が更新したモデル</button>{hasFilters && <button className="text-button" disabled={deleting} onClick={() => changeFilters({...defaultModelFilters, pageSize: filters.pageSize})}><X size={13}/>検索条件をクリア</button>}</div>
    </section>
    <section className="model-library-results" aria-label="モデル一覧" aria-busy={loading}>
      <div className="model-results-toolbar"><p role="status" aria-live="polite">{loading ? 'モデルを読み込んでいます…' : error ? 'モデル一覧を取得できませんでした' : <><strong>{total.toLocaleString()}</strong> 件{hasFilters && data && <span> / 全 {data.total_all.toLocaleString()} 件</span>}</>}</p><button className="button small secondary" disabled={unavailable} onClick={() => {setSelected([]); setActionError(''); setRefresh(n => n + 1);}}><RefreshCw size={13} className={loading ? 'spin' : ''}/>一覧を更新</button></div>
      {!confirmation && actionError && <p className="model-action-error" role="alert">{actionError}</p>}
      {!loading && !error && !!data?.circuits.length && <div className="model-selection-toolbar">
        <label><input ref={selectAllRef} type="checkbox" aria-label="このページをすべて選択" disabled={unavailable} checked={selected.length === data.circuits.length} onChange={e => setSelected(e.target.checked ? data.circuits.map(model => model.id) : [])}/>このページをすべて選択</label>
        <span role="status" aria-live="polite">{selected.length} 件を選択</span>
        {!!selected.length && <button className="text-button" disabled={unavailable} onClick={() => setSelected([])}>選択を解除</button>}
        <button className="button small danger-outline model-delete-button" disabled={unavailable || !selected.length || !employee.trim()} onClick={() => {setActionError(''); setConfirmation(data.circuits.filter(model => selected.includes(model.id)));}}><Trash2 size={14}/>選択したモデルを削除</button>
        {!employee.trim() && <p className="model-delete-help">削除するには上部に社員番号を入力してください。</p>}
      </div>}
      {error ? <div className="model-library-empty" role="alert"><FolderOpen size={30}/><h3>一覧を取得できませんでした</h3><p>{error}</p><button className="button secondary" onClick={() => setRefresh(n => n + 1)}>再試行</button></div>
      : loading ? <div className="model-library-empty"><LoaderCircle size={28} className="spin"/><p>モデルを読み込み中</p></div>
      : !data?.circuits.length ? <div className="model-library-empty"><FolderOpen size={32}/><h3>{hasFilters ? '検索条件に一致するモデルがありません' : '保存済みモデルはまだありません'}</h3><p>{hasFilters ? 'キーワードや更新者、更新期間を変更してください。' : '編集画面でモデルを保存すると、ここに表示されます。'}</p><button className="button secondary" onClick={hasFilters ? () => changeFilters({...defaultModelFilters, pageSize: filters.pageSize}) : onBack}>{hasFilters ? '検索条件をクリア' : '編集画面に戻る'}</button></div>
      : <div className="model-table-wrap"><table className="model-table"><caption className="sr-only">保存済みモデルの検索結果</caption><thead><tr><th scope="col">モデル</th><th scope="col">作成者</th><th scope="col">更新者</th><th scope="col">更新日時</th><th scope="col">版</th><th scope="col"><span className="sr-only">操作</span></th></tr></thead><tbody>{data.circuits.map(model => <tr key={model.id} data-model-id={model.id} className={[model.id === currentId ? 'current' : '', selected.includes(model.id) ? 'selected' : ''].filter(Boolean).join(' ')}><td data-label="モデル"><div className="model-name"><input type="checkbox" aria-label={`${model.name}を選択`} checked={selected.includes(model.id)} disabled={unavailable} onChange={e => setSelected(ids => e.target.checked ? [...ids, model.id] : ids.filter(id => id !== model.id))}/><CircuitBoard size={18}/><button onClick={() => onOpen(model.id)} disabled={unavailable} aria-label={`${model.name}を開く`}>{model.name}</button>{model.id === currentId && <span className="kind-tag">編集中</span>}</div>{model.description && <p className="model-description" title={model.description}>{model.description}</p>}</td><td data-label="作成者">{model.created_by}</td><td data-label="更新者">{model.updated_by}</td><td data-label="更新日時"><time dateTime={model.updated_at}>{dateLabel(model.updated_at)}</time></td><td data-label="版"><span className="kind-tag">rev.{model.revision}</span></td><td className="model-row-action"><button className="button small secondary" onClick={() => onOpen(model.id)} disabled={unavailable} aria-label={`${model.name}を編集画面で開く`}>開く<ArrowRight size={13}/></button></td></tr>)}</tbody></table></div>}
      <nav className="model-pagination" aria-label="モデル一覧のページ送り"><label htmlFor="model-page-size">表示件数<select id="model-page-size" aria-label="表示件数" value={filters.pageSize} disabled={deleting} onChange={e => filter({pageSize: Number(e.target.value)})}><option value={25}>25件</option><option value={50}>50件</option><option value={100}>100件</option></select></label><span>{loading ? '—' : `${total ? (filters.page - 1) * filters.pageSize + 1 : 0}–${Math.min(filters.page * filters.pageSize, total)} / ${total.toLocaleString()} 件`}</span><div><button className="button small secondary" aria-label="前のページ" disabled={unavailable || !!error || filters.page <= 1} onClick={() => changeFilters({...filters, page: filters.page - 1})}><ChevronLeft size={14}/>前へ</button><span>{filters.page} / {pages}</span><button className="button small secondary" aria-label="次のページ" disabled={unavailable || !!error || filters.page >= pages} onClick={() => changeFilters({...filters, page: filters.page + 1})}>次へ<ChevronRight size={14}/></button></div></nav>
    </section>
    {confirmation && <div className="modal-backdrop" onMouseDown={e => {if (e.target === e.currentTarget && !deleting) setConfirmation(null);}}><section ref={dialogRef} className="modal compact model-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="model-delete-title" aria-describedby="model-delete-description">
      <div className="modal-header"><h2 id="model-delete-title">{confirmation.length} 件のモデルを削除しますか？</h2><button className="icon-button" aria-label="削除確認を閉じる" disabled={deleting} onClick={() => setConfirmation(null)}><X size={19}/></button></div>
      <p id="model-delete-description">選択した保存済みモデルを削除します。削除後は開き直せません。過去の計算結果・パラメータースタディは残ります。{confirmation.some(model => model.id === currentId) && <><br/>編集中の内容は保持します。削除後に保存すると、別のモデルとして保存されます。</>}</p>
      <ul className="model-delete-list">{confirmation.map(model => <li key={model.id}><span>{model.name}</span><span className="kind-tag">rev.{model.revision}</span></li>)}</ul>
      <p className="model-delete-employee">削除者の社員番号：{employee.trim()}</p>
      {actionError && <p className="model-action-error" role="alert">{actionError}</p>}
      <div className="modal-actions"><button ref={cancelRef} className="button secondary" disabled={deleting} onClick={() => setConfirmation(null)}>キャンセル</button><button className="button danger-outline" disabled={deleting || !employee.trim()} onClick={() => void deleteSelected()}>{deleting ? <LoaderCircle size={15} className="spin"/> : <Trash2 size={15}/>} {deleting ? '削除中…' : `${confirmation.length} 件を削除`}</button></div>
    </section></div>}
  </main>;
}

import {useEffect, useState} from 'react';
import {ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, CircuitBoard, Copy, Filter, FolderOpen, LoaderCircle, RefreshCw, Search, X} from 'lucide-react';
import {api} from './types';
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

export default function ModelLibrary({filters, onFiltersChange, employee, currentId, currentName, dirty, busy, onOpen, onBack, onCopy, onTotal}: {
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
}) {
  const [data, setData] = useState<CircuitList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
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

  function filter(change: Partial<ModelFilters>) {onFiltersChange({...filters, ...change, page: 1});}
  const hasFilters = !!(filters.query || filters.updatedBy || filters.updatedWithin);
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / filters.pageSize));
  const unavailable = loading || busy;

  return <main className="model-library" aria-labelledby="model-library-title">
    <div className="model-library-heading">
      <div><span className="eyebrow">MODEL LIBRARY</span><h2 id="model-library-title">保存済みモデル</h2><p>共有された回路・プラズマモデルを検索して開けます。</p></div>
      <button className="button secondary" onClick={onBack} disabled={busy}><ArrowLeft size={15}/>編集画面に戻る</button>
    </div>
    <div className="current-model">
      <CircuitBoard size={18}/><div><span>編集中のモデル</span><strong>{currentName || '名称未設定'}</strong></div>
      {dirty && <span className="document-state unsaved">未保存</span>}
      {currentId && <button className="button small secondary" onClick={onCopy} disabled={busy}><Copy size={14}/>コピーとして保存</button>}
    </div>
    <section className="model-library-filters" aria-label="モデルの検索条件">
      <label className="model-query" htmlFor="model-query">モデルを検索<div className="model-search-input"><Search size={17}/><input id="model-query" aria-label="モデルを検索" type="search" placeholder="モデル名・メモ・社員番号" maxLength={200} value={filters.query} onChange={e => filter({query: e.target.value})}/>{filters.query && <button className="icon-button" aria-label="モデル検索をクリア" onClick={() => filter({query: ''})}><X size={15}/></button>}</div></label>
      <label htmlFor="model-updated-by">更新者（社員番号）<input id="model-updated-by" aria-label="更新者（社員番号）" value={filters.updatedBy} maxLength={80} placeholder="すべての更新者" onChange={e => filter({updatedBy: e.target.value})}/></label>
      <label htmlFor="model-updated-within">更新期間<select id="model-updated-within" aria-label="更新期間" value={filters.updatedWithin} onChange={e => filter({updatedWithin: e.target.value})}><option value="">全期間</option><option value="7">過去7日</option><option value="30">過去30日</option><option value="90">過去90日</option></select></label>
      <label htmlFor="model-sort">並べ替え<select id="model-sort" aria-label="並べ替え" value={filters.sort} onChange={e => filter({sort: e.target.value})}><option value="updated_desc">更新が新しい順</option><option value="updated_asc">更新が古い順</option><option value="name_asc">モデル名の昇順</option><option value="name_desc">モデル名の降順</option><option value="created_desc">作成が新しい順</option></select></label>
      <div className="model-filter-actions"><button className="button small secondary" disabled={!employee.trim()} onClick={() => filter({updatedBy: employee.trim()})}><Filter size={13}/>自分が更新したモデル</button>{hasFilters && <button className="text-button" onClick={() => onFiltersChange({...defaultModelFilters, pageSize: filters.pageSize})}><X size={13}/>検索条件をクリア</button>}</div>
    </section>
    <section className="model-library-results" aria-label="モデル一覧" aria-busy={loading}>
      <div className="model-results-toolbar"><p role="status" aria-live="polite">{loading ? 'モデルを読み込んでいます…' : error ? 'モデル一覧を取得できませんでした' : <><strong>{total.toLocaleString()}</strong> 件{hasFilters && data && <span> / 全 {data.total_all.toLocaleString()} 件</span>}</>}</p><button className="button small secondary" disabled={unavailable} onClick={() => setRefresh(n => n + 1)}><RefreshCw size={13} className={loading ? 'spin' : ''}/>一覧を更新</button></div>
      {error ? <div className="model-library-empty" role="alert"><FolderOpen size={30}/><h3>一覧を取得できませんでした</h3><p>{error}</p><button className="button secondary" onClick={() => setRefresh(n => n + 1)}>再試行</button></div>
      : loading ? <div className="model-library-empty"><LoaderCircle size={28} className="spin"/><p>モデルを読み込み中</p></div>
      : !data?.circuits.length ? <div className="model-library-empty"><FolderOpen size={32}/><h3>{hasFilters ? '検索条件に一致するモデルがありません' : '保存済みモデルはまだありません'}</h3><p>{hasFilters ? 'キーワードや更新者、更新期間を変更してください。' : '編集画面でモデルを保存すると、ここに表示されます。'}</p><button className="button secondary" onClick={hasFilters ? () => onFiltersChange({...defaultModelFilters, pageSize: filters.pageSize}) : onBack}>{hasFilters ? '検索条件をクリア' : '編集画面に戻る'}</button></div>
      : <div className="model-table-wrap"><table className="model-table"><caption className="sr-only">保存済みモデルの検索結果</caption><thead><tr><th scope="col">モデル</th><th scope="col">作成者</th><th scope="col">更新者</th><th scope="col">更新日時</th><th scope="col">版</th><th scope="col"><span className="sr-only">操作</span></th></tr></thead><tbody>{data.circuits.map(model => <tr key={model.id} data-model-id={model.id} className={model.id === currentId ? 'current' : ''}><td data-label="モデル"><div className="model-name"><CircuitBoard size={18}/><button onClick={() => onOpen(model.id)} disabled={unavailable} aria-label={`${model.name}を開く`}>{model.name}</button>{model.id === currentId && <span className="kind-tag">編集中</span>}</div>{model.description && <p className="model-description" title={model.description}>{model.description}</p>}</td><td data-label="作成者">{model.created_by}</td><td data-label="更新者">{model.updated_by}</td><td data-label="更新日時"><time dateTime={model.updated_at}>{dateLabel(model.updated_at)}</time></td><td data-label="版"><span className="kind-tag">rev.{model.revision}</span></td><td className="model-row-action"><button className="button small secondary" onClick={() => onOpen(model.id)} disabled={unavailable} aria-label={`${model.name}を編集画面で開く`}>開く<ArrowRight size={13}/></button></td></tr>)}</tbody></table></div>}
      <nav className="model-pagination" aria-label="モデル一覧のページ送り"><label htmlFor="model-page-size">表示件数<select id="model-page-size" aria-label="表示件数" value={filters.pageSize} onChange={e => filter({pageSize: Number(e.target.value)})}><option value={25}>25件</option><option value={50}>50件</option><option value={100}>100件</option></select></label><span>{loading ? '—' : `${total ? (filters.page - 1) * filters.pageSize + 1 : 0}–${Math.min(filters.page * filters.pageSize, total)} / ${total.toLocaleString()} 件`}</span><div><button className="button small secondary" aria-label="前のページ" disabled={unavailable || !!error || filters.page <= 1} onClick={() => onFiltersChange({...filters, page: filters.page - 1})}><ChevronLeft size={14}/>前へ</button><span>{filters.page} / {pages}</span><button className="button small secondary" aria-label="次のページ" disabled={unavailable || !!error || filters.page >= pages} onClick={() => onFiltersChange({...filters, page: filters.page + 1})}>次へ<ChevronRight size={14}/></button></div></nav>
    </section>
  </main>;
}

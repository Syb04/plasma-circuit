export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export interface Component {
  id: string; kind: string; label: string; ports: string[];
  parameters: Record<string, Json>; position: {x: number; y: number}; rotation: number;
}
export interface Wire { id: string; source: {component_id: string; port: string}; target: {component_id: string; port: string}; }
export interface CircuitDocument {
  schema_version: number; name: string; description: string; components: Component[]; wires: Wire[];
  parameters: Record<string, Json>; models: {name: string; definition: string}[];
}
export type AnalysisKind = 'op' | 'dc' | 'ac' | 'transient' | 'ccp' | 'global' | 'global_transient' | 'radial';
export interface Analysis {kind: AnalysisKind; settings: Record<string, Json>}
export interface CatalogComponent {kind: string; label: string; category: string; ports: string[]; parameters: Record<string, Json>; description?: string}
export interface Preset {id: string; name: string; description: string; document: CircuitDocument; analysis: Analysis}
export interface CircuitSummary {id: string; name: string; revision: number; created_by: string; updated_by: string; updated_at: string}
export interface SavedCircuit extends CircuitSummary {document: CircuitDocument}
export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | 'timed_out';
export interface Result {
  kind: string; converged: boolean; summary: Record<string, Json>;
  axis?: {name: string; unit: string; values: number[]}; signals: {name: string; unit: string; values: number[]}[];
  tables?: Json[]; logs?: string[]; netlist?: string; solver?: Record<string, Json>;
  model_metadata?: Record<string, Json>; diagnostics?: Record<string, Json>; rf_diagnostics?: Record<string, Json>; iedf?: Result;
}
export interface Run {id: string; status: RunStatus; circuit_id: string; circuit_revision: number; employee_id: string; analysis: Analysis; created_at: string; error?: string; result?: Result; cancel_requested?: boolean}
export class ApiError extends Error { constructor(public status: number, message: string) {super(message)} }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {...options, headers: {'Content-Type': 'application/json', ...options.headers}});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail ?? payload.error ?? `HTTP ${response.status}`;
    throw new ApiError(response.status, typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2));
  }
  return payload as T;
}
export function clone<T>(value: T): T { return structuredClone(value); }
let fallbackIdCounter=0;
export function createId(prefix:string):string {
  const cryptoApi=globalThis.crypto;
  if(typeof cryptoApi?.randomUUID==='function')return `${prefix}_${cryptoApi.randomUUID().replaceAll('-','')}`;
  if(typeof cryptoApi?.getRandomValues==='function'){
    const bytes=cryptoApi.getRandomValues(new Uint8Array(16));
    return `${prefix}_${Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('')}`;
  }
  fallbackIdCounter+=1;
  return `${prefix}_${Date.now().toString(16)}_${fallbackIdCounter.toString(16)}_${Math.random().toString(16).slice(2)}`;
}
export function engineering(value: number, unit = ''): string {
  if (!Number.isFinite(value)) return String(value);
  if (value === 0) return `0${unit ? ' ' + unit : ''}`;
  if (!unit) return Math.abs(value) < 1e-3 || Math.abs(value) > 1e5 ? value.toExponential(3) : String(Number(value.toPrecision(4)));
  const prefixes: Record<number,string> = {'-12':'p','-9':'n','-6':'µ','-3':'m','0':'','3':'k','6':'M','9':'G','12':'T'};
  const exponent = Math.max(-12, Math.min(12, Math.floor(Math.log10(Math.abs(value)) / 3) * 3));
  const number = Number((value / 10 ** exponent).toPrecision(4));
  return `${number}${unit ? ' ' + prefixes[exponent] + unit : ''}`;
}
export const analysisNames: Record<AnalysisKind,string> = {op:'DC動作点',dc:'DCスイープ',ac:'AC小信号解析',transient:'過渡解析',ccp:'CCP・固定プラズマ',global:'プラズマ・グローバル解析',global_transient:'プラズマ・時間発展0D',radial:'径方向・分布回路解析'};
export function isPlasmaAnalysis(kind:AnalysisKind):boolean {return ['ccp','global','global_transient','radial'].includes(kind);}
export function isPrescribedPower(analysis:Analysis):boolean {
  if(!['global','global_transient'].includes(analysis.kind))return false;
  if(analysis.settings.power_mode!==undefined)return analysis.settings.power_mode==='prescribed_absorbed';
  return analysis.settings.electron_heating_model==='prescribed_power'||analysis.kind==='global_transient';
}
export function analysisLabel(analysis:Analysis):string {
  if(!isPrescribedPower(analysis))return analysisNames[analysis.kind];
  const gas=analysis.settings.gas==='O2'?'O₂':String(analysis.settings.gas??'Ar');
  return `${gas}・指定電力${analysis.kind==='global_transient'?'時間発展':''}0D反応計算`;
}
export const statusNames: Record<RunStatus,string> = {queued:'待機中',running:'計算中',succeeded:'完了',failed:'失敗',canceled:'停止済み',timed_out:'時間上限'};
export const defaultSettings: Record<AnalysisKind, Record<string,Json>> = {
  op:{}, dc:{source:'v1', start:0, stop:5, step:0.1}, ac:{start_frequency:10,stop_frequency:1e8,points:50,variation:'dec'},
  transient:{time_step:1e-6,stop_time:0.005},
  ccp:{gas:'Ar',frequency_hz:40e6,rf_peak_voltage:250,pressure_pa:1.333223684,gap_m:0.05,gas_temperature_k:300,cathode_diameter_m:0.3,area_ratio:5,electron_density_m3:1e16,electron_temperature_ev:3},
  global:{gas:'Ar',frequency_hz:40e6,rf_peak_voltage:250,pressure_pa:1.333223684,gap_m:0.05,gas_temperature_k:300,cathode_diameter_m:0.3,area_ratio:5,electron_density_m3:1e16,electron_temperature_ev:3},
  global_transient:{gas:'Ar',frequency_hz:40e6,rf_peak_voltage:250,pressure_pa:1.333223684,gap_m:0.05,gas_temperature_k:300,cathode_diameter_m:0.3,area_ratio:5,electron_density_m3:1e16,electron_temperature_ev:3,power_mode:'prescribed_absorbed',absorbed_power_w:500,stop_time_s:0.001,output_points:201},
  radial:{gas:'Ar',frequency_hz:40e6,rf_peak_voltage:250,pressure_pa:1.333223684,gap_m:0.05,gas_temperature_k:300,cathode_diameter_m:0.3,area_ratio:5,electron_density_m3:1e16,electron_temperature_ev:3,radial_cells:32,radial_feed:'center',electrode_sheet_resistance_ohm:0.02,electrode_sheet_inductance_h:2e-9,ion_density_m3:1e16,mean_cathode_sheath_voltage_v:100,mean_anode_sheath_voltage_v:30},
};
export interface StudyAxis {path:string;values:number[]}
export interface StudyCase {id:string;index:number;coordinates:Record<string,Json>;status:RunStatus;run_id?:string;attempts:{number:number;run_id:string;status:RunStatus;error?:string}[]}
export interface Study {id:string;name:string;status:string;circuit_id:string;circuit_revision:number;axes:StudyAxis[];analysis?:Analysis;cases?:StudyCase[];counts:Partial<Record<RunStatus,number>>;created_at?:string;cancel_requested?:boolean;case_count?:number}
export interface Comparison {runs:{id:string;status:RunStatus;summary:Record<string,Json>;analysis:Analysis}[];input_differences:{path:string;values:Record<string,Json>}[];phase_alignment:{aligned:boolean;reason?:string;frequencies_hz?:number[]};waveforms:{run_id:string;axis?:{name:string;unit:string;values:number[]}|null;signals:{name:string;unit:string;values:number[]}[]}[]}
export interface Benchmark {id:string;name:string;material:string;measurement_definition:string;frequency_hz?:number;pressure_pa?:number;provenance:string|Record<string,Json>;metrics:{metric:string;value:number;unit:string;uncertainty?:number}[]}
export async function downloadApi(path:string,filename:string){const response=await fetch(`/api${path}`);if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(typeof body.detail==='string'?body.detail:'エクスポートに失敗しました。');}const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=filename;a.click();URL.revokeObjectURL(url);}
export function emptyDocument(): CircuitDocument {return {schema_version:1,name:'新しい回路',description:'',components:[],wires:[],parameters:{},models:[]};}

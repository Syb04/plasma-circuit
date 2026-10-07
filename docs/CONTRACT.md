# Implementation contract

The initial product is a Japanese UI for browser circuit editing, real PySpice/ngspice calculation, EDDs, CCP/global-model runs, and PostgreSQL persistence. No authentication; a non-empty employee ID is required for every save/run. Preserve leading zeros. Do not claim a physics model has been validated when it has not.

## Circuit document (JSON)

```json
{
  "schema_version": 1,
  "name": "RC example",
  "description": "",
  "components": [
    {"id":"v1","kind":"V","label":"V1","ports":["p","n"],"parameters":{"dc":5},"position":{"x":0,"y":0},"rotation":0},
    {"id":"r1","kind":"R","label":"R1","ports":["p","n"],"parameters":{"value":1000},"position":{"x":200,"y":0},"rotation":0},
    {"id":"gnd","kind":"GND","label":"GND","ports":["g"],"parameters":{},"position":{"x":0,"y":200},"rotation":0}
  ],
  "wires": [
    {"id":"w1","source":{"component_id":"v1","port":"p"},"target":{"component_id":"r1","port":"p"}}
  ],
  "parameters": {},
  "models": [{"name":"DDEFAULT","definition":".model DDEFAULT D(Is=1e-14 N=1)"}]
}
```

Electrical connections are unions of explicit port endpoints, never inferred from pixel crossings. GND endpoints belong to net `0`. A JUNCTION has port `p`. Several wires may share a port. Positions/rotation are display data. IDs must be stable and unique. R/C/L use `value` in SI. V/I use `dc`, `ac_magnitude`, `ac_phase`, and optional `waveform={kind:"sin"|"pulse"|"pwl",...}`. Catalog entries provide actual defaults/ports for every supported kind. Kind K has no ports and references inductors in parameters. Control-current references identify a voltage-source component. Components can contain `model` inside parameters; X has user-defined ports and a subcircuit model.

EDD parameters contain `branches:[{positive:"p1",negative:"n1",current:"V1/R",charge:"C0*V1"}]`, `parameters:{R:1000,C0:1e-9}`, and optional `intermediates:{...}`. Each branch has its own terminal pair. `Vk` is positive-minus-negative voltage; `Ik` is the conductive current expression of branch k. Total terminal current is Ik+dQk/dt. A Q expression may depend on other branch voltages and conductive currents. Expressions are a supported mathematical language, never arbitrary Python execution. Persist the exact model definitions in every run snapshot.

## Analysis JSON

`{"kind":"op"|"dc"|"ac"|"transient"|"ccp"|"global", "settings":{...}}`

- transient: `time_step`, `stop_time`, optional `max_step`, `start_time`, `initial_conditions`.
- ac: `start_frequency`, `stop_frequency`, `points`, `variation` (`dec`/`lin`/`oct`).
- dc: `source` component ID, `start`, `stop`, `step`.
- ccp/global: `gas` (Ar/O2/CF4, single gas), `frequency_hz:40000000`, `rf_peak_voltage:250`, `pressure_pa:1.333223684`, `gap_m:0.05`, `gas_temperature_k:300`, `cathode_diameter_m:0.3`, `area_ratio:5`, `electron_density_m3`, `electron_temperature_ev` for fixed CCP; global initial density/temperature settings. Model implementation specifies defaults for additional volume/wall-loss/chemistry settings and records assumptions. The CCP builtin preset is a parameterized template; it must not silently ignore a user-edited schematic. If the template is separate from arbitrary-circuit plasma coupling, say so explicitly in UI/result metadata.

## Simulation result JSON

```json
{
 "kind":"transient", "converged":true,
 "summary":{"key":1.0},
 "axis":{"name":"time","unit":"s","values":[0,1e-9]},
 "signals":[{"name":"V(out)","unit":"V","values":[0,1]}],
 "tables":[], "logs":[], "netlist":"...",
 "solver":{"pyspice":"1.5","ngspice":"..."},
 "model_metadata":{}, "diagnostics":{}
}
```

All values must be finite and JSON-serializable. OP can have an empty axis/signals and numerical tables/summary. AC signals expose magnitude/phase (separate named signals) rather than raw complex values. Results are bounded; plotting decimation must be stated. CSV export uses original saved result samples. Solver failures are errors, not manufactured results. `engine.execute_circuit(document:dict,analysis:dict)->dict`; `engine.build_netlist(document,analysis)->str`; `engine.simulate_netlist(netlist,analysis)->dict` is shared with the plasma module. `plasma.execute_plasma(document,analysis)->dict` handles ccp/global.

## HTTP API

- GET `/api/health`
- GET `/api/catalog`: `{components:[...],analyses:[...]}`; component entries `{kind,label,category,ports,parameters,...}`
- GET `/api/presets`: `{presets:[{id,name,description,document,analysis}]}`
- GET `/api/circuits`: `{circuits:[{id,name,revision,created_by,updated_by,created_at,updated_at}]}`
- POST `/api/circuits`: `{employee_id,document}` -> saved detail `{id,revision,document,...}`
- GET `/api/circuits/{id}`: saved detail
- PUT `/api/circuits/{id}`: `{employee_id,expected_revision,document}` -> saved detail; stale revision returns 409
- POST `/api/runs`: `{employee_id,circuit_id,expected_revision,analysis}` -> `{id,status,...}`. Snapshot exact saved circuit revision and employee ID before queueing.
- GET `/api/runs?circuit_id=...`: `{runs:[...]}`
- GET `/api/runs/{id}`: `{id,status,circuit_id,circuit_revision,employee_id,analysis,created_at,started_at,finished_at,error,result}` (result only available after success)
- POST `/api/runs/{id}/cancel`: marks cancel request and stops queued/running work
- GET `/api/runs/{id}/export.csv`

Statuses: queued/running/succeeded/failed/canceled/timed_out. PostgreSQL is the durable source of truth. Redis is the job queue. A worker runs solver work in a separate process with timeout/point/resource bounds. Worker crashes/timeouts/cancellation must leave an honest terminal status. Preserve circuit revisions and immutable run snapshots. Store compressed result bytes in PostgreSQL. API returns Japanese-readable error details with solver logs where available. Frontend save before run (including latest unsaved edits); employee ID can be remembered locally but remains editable. No access-control claims based on employee IDs.

## Ownership

- root: schemas.py, presets.py, requirements/build/compose, integration, README/spec, verification scripts.
- engine_impl: engine.py, expressions.py, catalog.py, engine/expression tests.
- global_impl: plasma.py, plasma_models.py, plasma tests and plasma-model documentation.
- api_impl: api.py, database.py, worker.py, API tests.
- frontend_impl: entire frontend except container files coordinated with root.

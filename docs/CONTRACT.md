# Implementation contract

The product has a Japanese UI for circuit editing, real PySpice/ngspice calculations, EDDs, CCP/global models, research workflows, and PostgreSQL persistence. It has no authentication. Circuit save, run creation, study creation/resume, and package import require a nonempty employee ID; trim surrounding whitespace and preserve leading zeros. Employee IDs record attribution and confer no access control. Reference registration and cancellation endpoints do not require an employee ID.

Model availability, numerical convergence, model-domain checks, reference agreement, and experimental validation are separate facts. A `succeeded` job may contain `converged=false`. No imported dataset, successful solve, or small reference error automatically establishes a validated plasma model.

## Circuit document JSON

```json
{
  "schema_version": 1,
  "name": "RC example", "description": "",
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

Connections are unions of explicit port endpoints, never inferred from pixel crossings. GND endpoints belong to net `0`; JUNCTION has port `p`. Several wires may share a port. Positions/rotation are display data. Component IDs are stable and unique, begin with a letter, and contain letters, digits, or underscores. R/C/L use SI `value`. V/I use `dc`, `ac_magnitude`, `ac_phase` and optional `waveform={kind:"sin"|"pulse"|"pwl",...}`. Voltage sources also accept `waveform.kind="rf"` with the periodic drive settings below. K has no ports and references inductors; control-current references identify a voltage-source component. X has user-defined ports and a subcircuit model. Catalog entries provide ports and defaults. Schema bounds are 500 components, 2000 wires, and 100 models.

Component duplication is available from the canvas toolbar, component inspector and Ctrl/Cmd+D in the editor. Deep-copy the applied component data, including nested parameters, ports and rotation; assign a fresh unique legal ID, a unique copy label of at most 100 Unicode code points, and a nearby unoccupied grid position. Keep existing wires, document parameters, models and analysis settings unchanged; the copy begins unconnected. References to voltage sources, inductors, named models and subcircuits retain their original targets. Document-level model definitions remain shared. Selecting the new component enables independent component edits; duplication is one undo/redo step and persists through ordinary save/load. Block duplication at 500 components. An invalid numeric draft in the selected component inspector blocks duplication, preserves its text and focuses the field. The shortcut ignores editable controls, modals and other workspace views, and suppresses held-key repeats. EDD branches and JSON editors require their existing Apply action before those drafts become component data to copy.

Numeric UI fields accept signed decimal/scientific notation (`e`/`E`), preserve incomplete drafts, and emit finite JSON numbers in SI units. Save/run checks mounted numeric fields for invalid drafts; changing time display units preserves physical seconds. D palette variants share kind `D`, ports `[p,n]`; the first catalog entry remains the native kind default. A D element with `parameters.model_parameters` generates a unique per-device `.model ... D(...)` instead of referencing `parameters.model`. Keys are case-insensitive with duplicates/unknowns rejected; supported keys are IS, N, RS, BV, IBV, CJO, VJ, M, TT, EG, XTI, FC, TNOM. Missing keys use ngspice defaults. The catalog initializes IS=1e-14, N=1, RS=0.1, CJO=1e-12; BV is absent. Area is a positive instance parameter emitted as `AREA=...`. Models and source-waveform numeric inputs persist in circuit revisions and immutable run snapshots; generated definitions are retained in the result netlist. Parameter units, ranges and UI instructions: [circuit parameters](circuit-parameters.md).

EDD parameters contain `branches:[{positive:"p1",negative:"n1",current:"V1/R",charge:"C0*V1"}]`, `parameters:{R:1000,C0:1e-9}`, and optional `intermediates:{...}`. Each branch has a terminal pair. `Vk` is positive-minus-negative voltage; `Ik` is conductive current, and total current is `Ik+dQk/dt`. Charge may depend on other branch voltages and conductive currents. Expressions use a restricted mathematical language, never arbitrary Python execution. Persist exact definitions in run snapshots.

`COAX` has fixed ports `[p1,n1,p2,n2]`, input/output positive inner-conductor terminals and a common ideal shield reference (`n1/n2` are united). Parameters are `inner_diameter_m=1e-3`, `shield_inner_diameter_m=3.35e-3` (shield **inner** diameter), `length_m=1`, `relative_permittivity=2.1`, `relative_permeability=1`, `loss_tangent=2e-4`, `inner_resistivity_ohm_m=1.724e-8`, `shield_resistivity_ohm_m=1.724e-8`, `shield_thickness_m=.15e-3`, `reference_frequency_hz=40e6`, and `segments=32`. Missing keys use these defaults; unknown keys, booleans, nonfinite or overflowing derived constants are rejected. Dimensions/material multipliers/frequency must be positive, shield inner diameter must exceed inner-conductor diameter, resistivities are nonnegative, loss tangent is `[0,1)`, segments is an integer 1–256 with at most 512 total coax sections per circuit. Numeric/scientific SI strings are accepted by the solver/preview; UI fields emit JSON numbers. Stamp a passive TEM pi ladder with a DC-preserving Foster skin-effect fit and a DC-blocking Debye dielectric-loss fit matched at the reference frequency. Both nonmagnetic conductors' internal impedances enter the differential loop; external common mode is excluded. Record finite constants, input parameters and assumptions under `model_metadata.coax_cables.<id>`. Terminal voltage/current signals include input/output; both currents are positive **into** the line. Net-input power includes storage change in transient analysis; AC uses peak-phasor average power. Periodic CCP additionally records periodic average net-input power. The loss fit is a reference-frequency approximation, not exact broadband material dispersion. See [coax cable](coax-cable.md).

`COAX_GND` is a separate shield-grounded variant with fixed visible ports `[p1,p2]`. Its electrical TEM terminals are `[node(p1),0,node(p2),0]`; the document, node map and UI contain no hidden `n1/n2` ports. Connect source/load returns to the usual circuit GND. Parameters, bounds, preview, solver stamp, signals and study selectors are shared with `COAX`; the total 512-section bound counts both variants. An implicitly grounded shield does not short the inner conductor or create a DC dielectric path, so the CCP DC-cluster traversal connects only p1 to p2. Result metadata additionally records `component_kind` (`COAX` or `COAX_GND`) and `shield_reference_node` (always `"0"` for the grounded variant). Keep existing `COAX` documents and catalog defaults unchanged; no DB migration or automatic conversion of existing models. Preset IDs are `coax` and `coax_grounded`.

## Analysis JSON and dispatch

```json
{"kind":"ccp","settings":{"gas":"Ar","frequency_hz":40000000,"rf_peak_voltage":250}}
```

`kind` accepts `op`, `dc`, `ac`, `transient`, `ccp`, `global`, `global_transient`, and `radial`. Settings are JSON data and each solver validates its own finite values and limits.

| Kind | Settings |
| --- | --- |
| `op` | Operating point |
| `dc` | Source component ID `source`, `start`, `stop`, `step` |
| `ac` | `start_frequency`, `stop_frequency`, `points`, `variation=dec/lin/oct` |
| `transient` | `time_step`, `stop_time`, optional `max_step`, `start_time`, `initial_conditions`, `use_initial_condition` |
| `ccp` | Fixed `electron_density_m3`, `electron_temperature_ev` and RF/geometry settings |
| `global` | Ar RF-coupled particle/electron balance; O₂ reduced chemistry or explicit `reaction_model` with `initial_species_densities_m3` |
| `global_transient` | Ar/O₂ actual BDF density/electron-energy/gas-heat integration |
| `radial` | Annular linear RF circuit with prescribed uniform density |

`simulation.execute_simulation(document,analysis)->dict` is the worker's shared entry point. It connects transport, surfaces and heating during RF/chemistry iterations, attaches RF/IEDF diagnostics where applicable, and runs optional numerical refinement. `engine.execute_circuit` handles ordinary analyses; `engine.build_netlist` and `engine.simulate_netlist` are shared with plasma stamping. `plasma.execute_plasma` handles core `ccp/global`; direct calls do not perform all common-entry extensions.

### Plasma topology and voltage

The supported product gases and presets are single Ar/O₂. CF₄ adoption and a built-in CF₄ chemistry model are deferred; the core fixed-CCP parser and saved-document compatibility retain CF₄. No mixed-gas or inferred molecular chemistry model is provided.

- Empty dedicated template: `document.parameters.builtin_ccp_template=1`, empty components/wires. Without `external_circuit`, impose electrode RF voltage and solve the zero-mean terminal-current DC bias. Reject added schematic elements.
- Empty template plus `settings.external_circuit`: construct the source/R/L/DC-block/shunt-C/PLASMA network. `rf_peak_voltage` is the ideal-source peak, not electrode amplitude.
- Ordinary schematic with exactly one `PLASMA` and ports `["p","n"]`: p is driven, n is return. Stamp the two sheaths and bulk in the real circuit. Analysis settings override identical PLASMA component settings; explicit schematic voltage-source waveform overrides analysis drive settings. `external_circuit` is invalid on this path.
- An isolated powered DC cluster requires a charge-holding block capacitor and uses periodic capacitor-charge shooting. Explicit DC-fed topology permits nonzero mean current. Multiple plasma loads are not supported.

Baseline RF/geometry inputs are `frequency_hz=40e6`, `rf_peak_voltage=250`, `pressure_pa=1.333223684`, `gap_m=.05`, `gas_temperature_k=300`, `cathode_diameter_m=.3`, `area_ratio=5`, `electron_density_m3=1e16`, `electron_temperature_ev=3`, `momentum_collision_frequency_hz=1e7`, `wall_edge_factor=.5`. Momentum frequency is ν in s⁻¹, without a 2π factor and without a verified gas-specific default source. Independently supplied `plasma_volume_m3`, `wall_loss_area_m2`, and geometry assumptions are recorded.

`external_circuit` allows `source_resistance_ohm` (omitted=50), `series_inductance_h`, `shunt_capacitance_f`, `dc_block_capacitance_f`, `dc_voltage_v`, `voltage_definition="source"`, `reference_impedance_ohm`. Zero R/L/C omits that element. Unknown external keys are rejected. Use `rf_source_id` to select among multiple voltage sources; `rf_source_port={component_id,port}` selects source p or the load side of its unique series output resistor. `source_reference_impedance_ohm` is a positive real source-plane Z₀, default 50 Ω.

Periodic RF drive settings include `second_frequency_hz`, `second_rf_peak_voltage`, `second_phase_deg`, `fundamental_frequency_hz`, `pulse_frequency_hz`, `pulse_duty_cycle`, `pulse_off_fraction`. All active frequencies must be integer multiples of the common fundamental. RF off fraction scales voltage amplitude. `rf` sources use an eight-common-period smoothstep; schematic `sin` sources preserve their explicit waveform. Periodic CCP sources must use `sin` without damping or `rf`.

RF cycles are 16–120, fastest-carrier `points_per_cycle` 64–512; global iterations 3–40. Ordinary requested points and retained internal RF vectors are bounded at 250000. Ordinary plotted result samples are bounded at 20000; the CCP wrapper retains the final two common periods for integration/results. Record sampling behavior rather than promising unbounded raw data.

### Optional physics and time integration

- `electron_transport`: `mode=explicit_nu` or `cross_section_eedf`; imported cross sections require energy eV, σ m², target/process and source. EEDF is a normalized energy PDF in eV⁻¹, Maxwellian or tabulated. Integrate rates and target collision frequencies without extrapolating cross sections. Do not claim a Boltzmann solve or automatically replace chemistry fits with transport-EEDF rates.
- `surface_parameters`: complete `cathode/anode/wall` entries with `material`, `state`, `temperature_k`, `gamma_o`, `gamma_metastable`, `secondary_electron_yield`, optional provenance/ranges. No material name fills missing coefficients. Local electrode secondary yields are used in the RF stamp; neutral wall probabilities feed the declared oxygen closure.
- `electron_heating`: `mode=bulk_drude` or `moving_wall_maxwellian`, optional `reflection_probability`, `edge_electron_density_m3`, budget tolerance and `max_rf_iterations` (2–24). The common-entry moving-wall path iterates a dissipative equivalent RF resistance and checks its measured power against the estimate. Keep reversible pressure work, capacitor work, ion acceleration and secondary transfer distinct.
- O₂ steady: `chemistry_model=oxygen_reduced` (also `oxygen`/`gudmundsson_2001` aliases). Common dispatch supplies the reduced selection when `gas=O2` and omitted. `power_mode=prescribed_absorbed` requires total `absorbed_power_w` and produces no RF waveform; default `rf_coupled` maps the actual electron/conductive/secondary ledger and RF ion acceleration. Reduced energy closure does not supply all 48 reaction energies. O₂ k20 requires exclusive `1<Te<4.5 eV`. `transport_mode=explicit_h/gudmundsson_2000` has independent model-domain diagnostics.
- Macro: `global_transient`, Ar/O₂; `power_mode=prescribed_absorbed/rf_coupled`, `absorbed_power_w`, `stop_time_s` (≤10), `output_points` (3–5001), `macro_relative_tolerance` (1e-9–1e-3), `macro_max_step_s`, populated initial state settings and gas heat inputs. RF mode refreshes actual carrier averages at `rf_update_interval_s` and pulse boundaries, then holds them. The configured refresh interval spans at least 10 common carrier periods, combined intervals ≤200; pulse/final splits may be shorter and need a separate time-scale check. Macro pulse off fraction scales averaged **power**; maximum 500 pulse periods. In schematic RF mode derive carrier frequencies from sources. Without explicit macro pulse controls, a unique source envelope inherits its OFF amplitude squared as a declared quadratic power approximation; it is not an off-state RF solve. Conflicting source envelopes require explicit macro controls. Strip source envelopes from carrier solves and apply the macro power envelope once. Initial pressure establishes inventory, subsequent pressure follows evolving neutrals and Tg. Domain termination returns partial history and honest diagnostics. No ignition claim.
- `iedf.enabled=true`: attaches trajectory results to RF CCP/global with a sheath waveform over the full common beat/pulse period. Controls include species, entrance densities, particle count, bins, RF/transit steps, seed, explicit thickness and constant charge-exchange cross section. Fixed-width, uniform-field reduced trajectories; sampling/convergence is recorded separately under `result.iedf` and IEDF nonconvergence also propagates to the parent result.
- Radial: `radial_cells`, `radial_feed=center/edge`, feed footprint, sheet R/L, mean sheath drops, ion entrance density and optional `operating_point`. Solve peak phasors and KCL/power balances. Density is prescribed uniform; no radial particle/chemistry transport or complete electromagnetic field solution.
- `numerical_validation`: `enabled`, `relative_tolerance`, optional selected `metrics`, `refine_points/refine_cycles`. Compare additional RF point/cycle, radial mesh or ODE tolerance runs while retaining baseline output. Errors, unconverged variants and absence of a refinement candidate are not passes. This is numerical validation, never physical validation.

See [plasma models](plasma-models.md), [electron/surface models](electron-surface-models.md), [ion/radial models](ion-radial-models.md) and [workflow examples](analysis-workflows.md) for equations and import structures.

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

Numeric values must be finite and JSON-serializable; genuinely unavailable moments/ratios may be null. OP and prescribed-power steady output may have empty axis/signals and numerical tables/summary. AC exposes magnitude/phase rather than raw complex values. Macro and radial results use their own axis and solver information. CSV exports saved samples/tables; browser-only plotting decimation does not change them. Solver failures are errors, not manufactured successful results.

Optional `rf_diagnostics` contains `frequency_hz`, `cycles_used`, peak-phasor convention, `measurement_planes`, quality and assumptions. Each plane names voltage/current signals, real/imag fundamental Z, phase, RMS, mean/DC/fundamental power, power factor, THD and harmonic records. Directional powers appear only with explicit positive real Z₀. Use adaptive-time integration over integer common periods; disclose truncated harmonic resolution. Positive current enters the measured load. Large-signal `V1/I1` differs from small-signal AC.

`model_metadata` records input settings, geometry, selected models, versions, sources, imported data and assumptions. `diagnostics` holds numerical residuals, applicability warnings, iteration history, macro termination and optional refinement. `iedf` is a nested result with energy axis, PDFs, species tables and its own convergence.

## HTTP API

All paths use `/api`. PostgreSQL is durable; Redis queues solver work. Run snapshots and prior circuit revisions are immutable.

| Method/path | Request / response |
| --- | --- |
| GET `/health` | Service health |
| GET `/catalog` | `{components:[...],analyses:[...]}` with actual supported defaults/ports |
| GET `/presets` | `{presets:[{id,name,description,document,analysis}]}` |
| POST `/coax/preview` | `{parameters:{...}}` → finite derived TEM/loss constants, parameters, warnings and assumptions; read-only, no employee ID, DB write or queue job |
| GET `/circuits` | `{circuits:[{id,name,description,revision,created_by,updated_by,created_at,updated_at}],total,total_all,limit,offset}` |
| POST `/circuits` | `{employee_id,document}` → saved circuit detail |
| POST `/circuits/delete` | `{employee_id,circuits:[{id,expected_revision}]}` → `{deleted_ids,deleted_at}`; atomic batch deletion |
| GET `/circuits/{id}` | Saved detail with document |
| PUT `/circuits/{id}` | `{employee_id,expected_revision,document}` → detail; stale revision 409 |
| POST `/runs` | `{employee_id,circuit_id,expected_revision,analysis}` → queued run; snapshot saved revision before enqueue |
| GET `/runs?circuit_id=...` | Run list |
| GET `/runs/{id}` | Status/timestamps/error, analysis, snapshot, runtime config, result when succeeded |
| POST `/runs/{id}/cancel` | Cancel queued/running work |
| GET `/runs/{id}/export.csv` | Saved result samples or summary/tables; completed result required |
| POST `/studies` | Run-creation fields plus `name`, `axes:[{path,values}]`; durable cases before enqueue |
| GET `/studies?circuit_id=...` | Study list/counts |
| GET `/studies/{id}` | Snapshot, analysis, coordinates, latest case status/run and all attempts |
| POST `/studies/{id}/cancel` | Cancel pending cases and record study cancellation |
| POST `/studies/{id}/resume` | `{employee_id}` → new runs for failed/timed-out/canceled cases; no eligible cases 409 |
| GET `/studies/{id}/export.csv` | Latest attempt per case: index, run ID, attempt, status/error, SI coordinates and summaries |
| POST `/compare` | `{run_ids:[...],phase_align:true}` → 2–4 distinct runs, flattened input differences and waveforms/alignment status |
| POST `/benchmarks` | Reference metadata and CSV/JSON metric data → registered reference |
| GET `/benchmarks`, `/benchmarks/{id}` | Reference list/detail |
| POST `/benchmarks/{id}/compare` | `{run_id}` → metric errors, uncertainty checks, missing metrics and warnings; completed run required |
| GET `/runs/{id}/package` | Analysis package including original status/error |
| POST `/packages/import` | `{employee_id,package}` → new circuit, analysis/provenance, verification and `requires_recalculation=true`, `result=null` |

Circuit-list queries accept `q` (up to 200 characters; case-insensitive literal substring of name, description, creator or updater), `updated_by` (exact employee ID, up to 80 characters), `updated_within_days` (1–3650 days), `sort` (`updated_desc`, `updated_asc`, `name_asc`, `name_desc`, `created_desc`), `limit` (1–100) and `offset` (nonnegative). Filters combine with AND; search fields combine with OR. `total` counts matching, non-deleted models before pagination and `total_all` counts all non-deleted saved models. Ordering has an ID tie-breaker. List entries contain metadata and descriptions, never complete circuit documents. The UI always requests a bounded page; omitting `limit` preserves the original API behavior for existing clients.

Model deletion accepts 1–100 distinct IDs with a strictly positive integer `expected_revision` and a nonblank employee ID (trimmed, leading zeroes preserved). Unknown request/target fields are rejected. The transaction locks targets in ID order and checks every revision; a missing ID returns 404, an already deleted or updated target returns 409, and either failure deletes none. Conditional updates also guard against concurrent changes. Deletion records `circuits.deleted_at` and `deleted_by` without changing the last saved revision or document. Deleted models cannot be listed, opened, updated, or used to create new runs/studies (detail/update/new-job endpoints return 404). Existing circuit revisions, queued/running jobs, results, studies/attempts, references and import provenance remain intact. Historical run/study lookup, CSV/package export, comparison and retry of existing study cases remain available using their immutable snapshots. This is a logical deletion; there is no restore or physical purge API. Existing PostgreSQL/SQLite tables gain nullable deletion columns in place at startup; PostgreSQL uses a transaction advisory lock to serialize API/worker initialization.

Run states are `queued/running/succeeded/failed/canceled/timed_out`. Studies derive `queued/running/succeeded/partial_failed/canceled` from latest attempts and cancellation. A queue failure stays a durable failed case. Resume uses the previous attempt's input snapshot and creates a new run/attempt with current runtime provenance, preserving failures and diagnostics.

Studies accept one or two distinct numeric selector axes and at most 100 Cartesian-product cases. API coordinates are SI values. Paths are whitelisted numeric settings, supported existing numeric component parameters, or the six source-waveform selectors below; they cannot rewrite arbitrary expressions/models or nested objects. Integer settings require integer values. UI provides a subset and converts its displayed mTorr/MHz units to SI.

For `COAX` and `COAX_GND`, all eleven scalar keys above are supported existing numeric component selectors via `document.components.<id>.parameters.<key>`. `segments` requires an integer 1–256; other geometry/material constraints are checked by the solver in each case. The UI converts displayed mm/MHz to m/Hz and retains SI coordinates in study/run snapshots.

Source selectors use `document.components.<id>.parameters.waveform.<field>`. For `sin` on V/I sources, fields are `frequency` and `amplitude`. For `rf` on V sources, fields are `frequency_hz`, `rf_peak_voltage`, `second_frequency_hz`, and `second_rf_peak_voltage`. Each selected field must already exist as a number; missing fields, booleans, numeric strings, unsupported source/waveform kinds, and other waveform fields are rejected. In authored PLASMA schematics for `ccp/global/global_transient`, ignored analysis RF-drive selectors such as `analysis.settings.frequency_hz` and `analysis.settings.rf_peak_voltage` are rejected with guidance to select the existing source waveform path. Empty dedicated templates retain supported analysis-setting axes. Radial analysis uses independent settings and is not subject to this authored-PLASMA source restriction.

Reference creation requires `name`, `material`, `measurement_definition`, positive finite `frequency_hz` and `pressure_pa`, nonempty `provenance`, `format=csv/json`, `data`, optional nonnegative `uncertainty`. CSV needs `metric,value,unit`; per-row `uncertainty` is optional. JSON accepts a metrics list or `{metrics:[...]}`. References are limited to 1 MiB and 1–200 distinct metrics. Supported units are explicitly converted to canonical units. Missing metrics or incompatible dimensions are reported; no inferred measurement-definition conversion occurs. Zero reference values have null relative error. Agreement is metric-specific and does not set experimental-validation status.

Packages use `format=plasma-circuit-analysis`, `format_version=1`, `hash_algorithm=sha256-json-binary64-v1`, input document/analysis/revision, runtime provenance, historical result/status/error, content SHA-256 hashes and a package SHA-256. The declared algorithm sorts object keys and hashes finite JSON numbers by their binary64 value, so browser changes in equivalent numeric spelling preserve integrity; booleans remain a separate type. Packages without `hash_algorithm`, or with `null`, use the legacy JSON-serialization digest; other algorithm values are rejected. Import size limit is 32 MiB. Verify schema and all hashes, save a new circuit revision and package provenance, and require recalculation. Imported historical results do not create succeeded runs. `hashes_verified=true` and `authenticity_verified=false` explicitly distinguish integrity from issuer authentication.

The worker performs calculations in a separate process with runtime/point/resource bounds. Crashes, timeouts and cancellation must retain honest terminal status. Result bytes are compressed in PostgreSQL. Save-before-run includes the current editor content; employee ID may be remembered locally but remains editable. Validation evidence belongs in [validation status](validation-status.md), with current counts and browser checks stated only when actually verified.

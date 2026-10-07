"""Macroscopic 0-D time integration with explicit power and gas heat budgets.

This is a populated, quasineutral Maxwellian plasma model, not ignition or
PIC. RF mode solves a cycle-periodic circuit at explicit refresh times and
holds its heating over the next macro interval. All plotted state samples
come from solve_ivp; no steady-state interpolation creates a time history.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Callable, Mapping

import numpy as np
from scipy.integrate import solve_ivp

from .oxygen_adapter import _ImposedIonPowerModel, oxygen_inputs, oxygen_rf_settings, rf_power_budget, rf_secondary_yield, solve_oxygen_absorbed
from .oxygen_study import ALL_NAMES, HEAVY_NAMES, _CaseModel, sccm_to_particles_s, study_metadata
from .plasma_models import ATOMIC_MASS, BOLTZMANN as KB, ELEMENTARY_CHARGE as E, ELECTRON_MASS as ME, argon_rates


def _finite(value: Any, label: str, *, lower: float = 0, inclusive: bool = False) -> float:
    number = float(value)
    if not math.isfinite(number) or (number < lower if inclusive else number <= lower):
        raise ValueError(f"{label} must be finite and {'≥' if inclusive else '>'} {lower:g}")
    return number


@dataclass(frozen=True)
class PowerPulse:
    frequency_hz: float | None = None
    duty_cycle: float = .5
    off_fraction: float = 0

    @classmethod
    def parse(cls, settings: Mapping[str, Any]) -> "PowerPulse":
        frequency = settings.get("pulse_frequency_hz")
        frequency = None if frequency is None else _finite(frequency, "pulse_frequency_hz")
        duty = _finite(settings.get("pulse_duty_cycle", .5), "pulse_duty_cycle")
        off = _finite(settings.get("pulse_off_fraction", 0), "pulse_off_fraction", inclusive=True)
        if duty > 1 or off > 1:
            raise ValueError("Pulse duty must be in (0,1], off fraction in [0,1]")
        return cls(frequency, duty, off)

    def factor(self, t: float) -> float:
        if self.frequency_hz is None or self.duty_cycle == 1:
            return 1.0
        phase = (t*self.frequency_hz) % 1
        if abs(phase-1) < 1e-12:
            phase = 0.0
        if abs(phase-self.duty_cycle) < 1e-12:
            phase = self.duty_cycle
        return 1.0 if phase < self.duty_cycle else self.off_fraction

    def breakpoints(self, stop: float) -> list[float]:
        if self.frequency_hz is None or self.duty_cycle == 1:
            return []
        cycles = math.ceil(stop*self.frequency_hz)
        if cycles > 500:
            raise ValueError("Macro run permits at most 500 pulse periods; RF carriers are cycle averaged")
        return sorted({time for j in range(cycles+1)
            for time in (j/self.frequency_hz, (j+self.duty_cycle)/self.frequency_hz) if 0 < time < stop})


class GlobalTransientModel:
    """Physical density, electron energy and Tg RHS with integrated budgets."""
    def __init__(self, settings: Mapping[str, Any]):
        self.settings = dict(settings)
        self.gas = str(settings.get("gas", "Ar"))
        if self.gas not in {"Ar", "O2"}:
            raise ValueError("Macro global chemistry currently supports Ar and O₂")
        self.mode = settings.get("power_mode", "prescribed_absorbed")
        if self.mode not in {"prescribed_absorbed", "rf_coupled"}:
            raise ValueError("power_mode must be prescribed_absorbed or rf_coupled")
        self.power_w = _finite(settings.get("absorbed_power_w", 500), "absorbed_power_w")
        self.initial_tg = _finite(settings.get("initial_gas_temperature_k", settings.get("gas_temperature_k", 300)), "initial_gas_temperature_k")
        self.background_tg = _finite(settings.get("background_gas_temperature_k", settings.get("gas_temperature_k", 300)), "background_gas_temperature_k")
        self.feed_tg = _finite(settings.get("feed_temperature_k", self.background_tg), "feed_temperature_k")
        self.radius = _finite(settings.get("radius_m", float(settings.get("cathode_diameter_m", .3))/2), "radius_m")
        self.length = _finite(settings.get("length_m", settings.get("gap_m", .05)), "length_m")
        self.volume = math.pi*self.radius**2*self.length
        self.area = _finite(settings.get("wall_loss_area_m2", 2*math.pi*self.radius*(self.radius+self.length)), "wall_loss_area_m2")
        self.pressure = _finite(settings.get("pressure_pa", 1.333223684), "pressure_pa")
        self.nref = self.pressure/(KB*self.initial_tg)
        self.flow = _finite(settings.get("flow_sccm", 50), "flow_sccm")
        self.feed = sccm_to_particles_s(self.flow)
        self.pump = self.feed/(self.nref*self.volume)
        self.edge = _finite(settings.get("wall_edge_factor", .5), "wall_edge_factor")
        if self.edge > 1:
            raise ValueError("wall_edge_factor must be ≤1")
        self.nu = _finite(settings.get("momentum_collision_frequency_hz", 1e7), "momentum_collision_frequency_hz", inclusive=True)
        self.mass = 39.948
        self.conductance = _finite(settings.get("gas_wall_conductance_w_k", 0), "gas_wall_conductance_w_k", inclusive=True)
        self.inelastic_fraction = _finite(settings.get("gas_inelastic_heating_fraction", 0), "gas_inelastic_heating_fraction", inclusive=True)
        self.ion_fraction = _finite(settings.get("gas_ion_heating_fraction", 0), "gas_ion_heating_fraction", inclusive=True)
        if max(self.inelastic_fraction, self.ion_fraction) > 1:
            raise ValueError("Gas heating fractions must be in [0,1]")
        self.cv = 1.5*KB if self.gas == "Ar" else 2.5*KB
        self.cp = self.cv+KB
        self.capacity = _finite(settings.get("gas_heat_capacity_j_k", self.cv*self.nref*self.volume), "gas_heat_capacity_j_k")
        self.temperature_domain = (1.0, 7.0) if self.gas == "Ar" else (1.0, 4.5)
        self.names = ("Ar", "Ar+") if self.gas == "Ar" else HEAVY_NAMES
        self.count = len(self.names)
        self.rf_electron_w, self.rf_ion_w = 0.0, 0.0
        self.rf_retarding_w, self.rf_total_w = 0.0, 0.0
        self.rf_secondary_yield = rf_secondary_yield(settings)
        self.rf_history: list[dict[str, Any]] = []
        self.domain_trials = 0
        self.initialization = "explicit populated plasma initial state"
        initial_ne = _finite(settings.get("initial_electron_density_m3", settings.get("electron_density_m3", 1e16)), "initial_electron_density_m3")
        initial_te = _finite(settings.get("initial_electron_temperature_ev", settings.get("electron_temperature_ev", 3)), "initial_electron_temperature_ev")
        explicit = settings.get("initial_species_densities_m3")
        if self.gas == "O2":
            if explicit is None:
                initial = solve_oxygen_absorbed(dict(settings) | {"gas_temperature_k": self.initial_tg}, self.power_w)
                if not initial["success"]:
                    raise ValueError("O₂ transient automatic steady initialization failed; provide valid populated initial_species_densities_m3 and Te or change assumptions")
                densities = np.asarray([initial["densities_m3"][name] for name in self.names])
                initial_te = initial["temperature_ev"]
                initial_ne = initial["electron_density_m3"]
                self.pump = initial["pumping_rate_s"]
                self.initialization = "numerically closed prescribed-total-power oxygen steady state at initial Tg; not ignition"
            else:
                if set(explicit) - set(ALL_NAMES):
                    raise ValueError("Unknown O₂ initial species")
                densities = np.asarray([_finite(explicit.get(name, 0), f"initial density {name}") for name in self.names])
                initial_ne = float(sum(densities[8:10])-sum(densities[5:8]))
                if "e" in explicit and not math.isclose(float(explicit["e"]), initial_ne, rel_tol=1e-8):
                    raise ValueError("O₂ initial electron density must satisfy quasineutrality")
            self.pump = _finite(settings.get("pumping_rate_s", self.pump), "pumping_rate_s")
        else:
            densities = np.asarray([self.nref, initial_ne]) if explicit is None else np.asarray([
                _finite(explicit.get(name, 0), f"initial density {name}") for name in self.names])
            initial_ne = float(densities[1])
        if not 1e10 < initial_ne <= 1e20:
            raise ValueError("Initial quasineutral ne must be >1e10 and ≤1e20 m^-3")
        lower, upper = self.temperature_domain
        if not lower < initial_te < upper:
            raise ValueError(f"Initial Te must be strictly within {lower:g}–{upper:g} eV")
        # Each density uses its own scale so minor negative species retain
        # accuracy; electron energy is a physical state, not prescribed Te.
        self.density_scales = np.maximum(densities, 1.0)
        self.energy_scale = 1.5*initial_ne*E*initial_te*self.volume
        self.oxygen_settings = dict(settings) | {"gas_temperature_k": self.initial_tg}
        self.initial = np.concatenate((densities/self.density_scales,
            [1.0, self.initial_tg], np.zeros(7)))
        # Integrals: electron input, electron loss, gas heat input, gas sinks,
        # total external power, ion RF acceleration, total external energy sink.

    def unpack(self, y: np.ndarray) -> tuple[np.ndarray, float, float, float]:
        density = y[:self.count]*self.density_scales
        ne = float(density[1]) if self.gas == "Ar" else float(sum(density[8:10])-sum(density[5:8]))
        energy_j = float(y[self.count])*self.energy_scale
        te = energy_j/(1.5*max(ne, 1)*E*self.volume)
        return density, ne, te, float(y[self.count+1])

    def _oxygen_state(self, density: np.ndarray, ne: float, te: float, tg: float) -> tuple[dict, _CaseModel]:
        case_settings = self.oxygen_settings | {"gas_temperature_k": tg}
        model = _ImposedIonPowerModel(oxygen_inputs(case_settings, self.power_w), None, case_settings)
        safe = np.maximum(density, 1e-20)
        safe_ne = max(ne, 1.0)
        x = np.asarray([math.log(n/model.neutral_reference) for n in safe[:8]] +
            [math.log(safe_ne/model.neutral_reference), math.log(safe[8]/safe[9]),
             math.log(te), math.log(self.pump/model.pump_reference)])
        state = model.evaluate(x)
        return state, model

    def evaluate(self, y: np.ndarray, pulse_factor: float = 1) -> dict[str, Any]:
        density, ne, raw_te, tg = self.unpack(y)
        lower, upper = self.temperature_domain
        # BDF trial states can cross a terminal event while locating it. Rates
        # use the boundary value solely for locating the event; no out-of-range
        # coefficient is evaluated or returned as an accepted model state.
        te = float(np.clip(raw_te, lower+1e-10, upper-1e-10))
        if te != raw_te:
            self.domain_trials += 1
        tg = max(tg, 1.0)
        safe = np.maximum(density, 0)
        ne_safe = max(ne, 1.0)
        electron_transport = None
        if self.gas == "O2":
            oxygen, model = self._oxygen_state(safe, ne_safe, te, tg)
            source = oxygen["production"][:10]-oxygen["loss"][:10]
            loss = oxygen["loss_w"]
            inelastic = loss["ionization"]+loss["excitation"]
            elastic, electron_wall = loss["elastic"], loss["electron_wall"]
            bohm = .5*te*oxygen["electron_wall_m3_s"]*self.volume*E
            floating_ion = loss["ion_wall"]-bohm
            phi = oxygen["phi"]
            wall_events = oxygen["electron_wall_m3_s"]
            transport = oxygen["transport"]
            electron_transport = oxygen.get("electron_transport")
            atoms = model.chemistry["atoms"][:10]
            atom_source = float(np.dot(atoms, source))
            atom_expected = 2*self.feed/self.volume-self.pump*float(np.dot(atoms[:5], safe[:5]))
        else:
            rates = argon_rates(te)
            iz = ne_safe*safe[0]*rates["ionization"]
            velocity = math.sqrt(E*te/(self.mass*ATOMIC_MASS))
            wall_events = ne_safe*self.edge*velocity*self.area/self.volume
            source = np.asarray([-iz+wall_events+self.feed/self.volume-self.pump*safe[0], iz-wall_events])
            inelastic = ne_safe*safe[0]*self.volume*E*(15.76*rates["ionization"]+11.55*rates["excitation"])
            nu = self.nu
            if self.settings.get("electron_transport", {}).get("mode") == "cross_section_eedf":
                from .electron_transport import resolve_electron_transport
                transport_e = resolve_electron_transport(self.settings["electron_transport"], electron_temperature_ev=te,
                    neutral_densities_m3={"Ar": safe[0]}, explicit_nu_s=self.nu)
                nu = transport_e["momentum_collision_frequency_s"]
                electron_transport = transport_e
            elastic = ne_safe*self.volume*3*ME/(self.mass*ATOMIC_MASS)*nu*E*max(te-KB*tg/E, 0)
            electron_wall, bohm = 2*te*wall_events*self.volume*E, .5*te*wall_events*self.volume*E
            phi = max(te*math.log(math.sqrt(E*te/(2*math.pi*ME))/(self.edge*velocity)), 0)
            floating_ion = phi*wall_events*self.volume*E
            transport = {"mode": "explicit_h", "edge_factor": self.edge}
            atom_source, atom_expected = float(sum(source)), self.feed/self.volume-self.pump*safe[0]
        if self.mode == "rf_coupled":
            electron_wall *= 1+self.rf_secondary_yield
            electron_input, ion_acceleration = self.rf_electron_w*pulse_factor, self.rf_ion_w*pulse_factor
            retarding = self.rf_retarding_w*pulse_factor
            if pulse_factor == 0:
                # RF-off afterglow retains its floating ambipolar sheath: ion
                # acceleration is drawn from the decaying electron energy.
                phi -= te*math.log1p(self.rf_secondary_yield)
                ion_acceleration = phi*wall_events*self.volume*E
                retarding = (1+self.rf_secondary_yield)*ion_acceleration
                electron_input = self.rf_secondary_yield*ion_acceleration
            electron_loss = inelastic+elastic+electron_wall+bohm+retarding
            total_input = self.rf_total_w*pulse_factor
        else:
            electron_input, ion_acceleration = self.power_w*pulse_factor, floating_ion
            electron_loss = inelastic+elastic+electron_wall+bohm+ion_acceleration
            total_input = electron_input
        # Elastic transfer enters gas once. Other partitions are explicit user
        # assumptions; defaults transfer none of excitation/ion deposition.
        gas_input = elastic+self.inelastic_fraction*inelastic+self.ion_fraction*(ion_acceleration+bohm)
        wall_sink = self.conductance*(tg-self.background_tg)
        flow_sink = self.feed*self.cp*(tg-self.feed_tg)
        gas_sink = wall_sink+flow_sink
        external_sink = electron_loss+gas_sink-gas_input+(total_input-electron_input)
        return {"density": density, "ne": ne, "te": raw_te, "tg": tg, "source": source,
            "electron_input_w": electron_input, "electron_loss_w": electron_loss,
            "gas_input_w": gas_input, "gas_sink_w": gas_sink, "wall_thermal_loss_w": wall_sink,
            "flow_enthalpy_loss_w": flow_sink, "total_input_w": total_input,
            "ion_acceleration_w": ion_acceleration, "external_sink_w": external_sink,
            "electron_retarding_sheath_work_w": retarding if self.mode == "rf_coupled" else floating_ion,
            "inelastic_w": inelastic, "elastic_w": elastic, "transport": transport,
            "floating_sheath_potential_v": phi,
            "electron_transport": electron_transport,
            "atom_source_relative_error": (atom_source-atom_expected)/max(abs(atom_source), abs(atom_expected), self.feed/self.volume),
            "pressure_pa": KB*tg*float(sum(safe[:5] if self.gas == "O2" else safe[:1]))}

    def rhs(self, t: float, y: np.ndarray, pulse_factor: float = 1) -> np.ndarray:
        state = self.evaluate(y, pulse_factor)
        source = np.asarray(state["source"])
        # Prevent a negative-state trial from being driven farther negative.
        # Accepted corrections are checked and reported by the runner.
        source = np.where((y[:self.count] <= 0) & (source < 0), 0, source)
        return np.concatenate((source/self.density_scales,
            [(state["electron_input_w"]-state["electron_loss_w"])/self.energy_scale,
             (state["gas_input_w"]-state["gas_sink_w"])/self.capacity],
            [state[key] for key in ("electron_input_w", "electron_loss_w", "gas_input_w", "gas_sink_w",
                                   "total_input_w", "ion_acceleration_w", "external_sink_w")]))

    def update_rf(self, t: float, y: np.ndarray, solver: Callable) -> None:
        from .plasma import CCPSettings, effective_rf_settings
        state = self.evaluate(y)
        # Envelope belongs to macro power input. The carrier solver receives
        # an unpulsed drive to avoid multiplying the pulse duty twice.
        rf_settings = self.settings | {"pulse_frequency_hz": None, "pulse_duty_cycle": 1, "pulse_off_fraction": 1,
            "pressure_pa": state["pressure_pa"]}
        if self.gas == "O2":
            oxygen, _ = self._oxygen_state(state["density"], state["ne"], state["te"], state["tg"])
            chemistry = {"densities_m3": dict(zip(ALL_NAMES, map(float, oxygen["densities"]))),
                "wall_rates_s": dict(zip(("k50", "k51", "k52", "k53", "k54"), map(float, oxygen["wall_rates"]))),
                "temperature_ev": state["te"], "electronegativity": sum(oxygen["densities"][5:8])/state["ne"], "volume_m3": self.volume,
                "metadata": {"electron_transport": oxygen.get("electron_transport", {})}}
            parsed = oxygen_rf_settings(rf_settings | {"gas_temperature_k": state["tg"], "wall_loss_area_m2": self.area}, chemistry)
        else:
            parsed = CCPSettings.parse(rf_settings | {"electron_density_m3": state["ne"],
                "electron_temperature_ev": state["te"], "gas_temperature_k": state["tg"],
                "plasma_volume_m3": self.volume, "wall_loss_area_m2": self.area})
            if "neutral_species_densities_m3" in parsed.__dataclass_fields__:
                parsed = replace(parsed, neutral_species_densities_m3={"Ar": float(state["density"][0])})
            if "ion_wall_current_density_a_m2" in parsed.__dataclass_fields__:
                velocity = math.sqrt(E*state["te"]/(self.mass*ATOMIC_MASS))
                chemistry_current = E*state["ne"]*self.edge*velocity*self.area
                parsed = replace(parsed, ion_wall_current_density_a_m2=chemistry_current/(parsed.cathode_area+parsed.anode_area))
        result = solver(parsed)
        actual = effective_rf_settings(parsed, result)
        self.rf_secondary_yield = (actual.secondary_electron_yield_cathode*actual.cathode_area+
            actual.secondary_electron_yield_anode*actual.anode_area)/(actual.cathode_area+actual.anode_area)
        target_nu = result.get("model_metadata", {}).get("electron_transport", {}).get("target_collision_frequencies_s", {})
        if self.settings.get("electron_transport", {}).get("mode") != "cross_section_eedf":
            if self.gas == "O2" and set(target_nu) == {"O", "O2"}:
                self.oxygen_settings["electron_momentum_nu_s"] = target_nu
            elif self.gas == "Ar":
                self.nu = actual.momentum_collision_frequency_hz
        summary = result["summary"]
        budget = rf_power_budget(summary)
        self.rf_electron_w = budget["electron_heating_w"]
        self.rf_ion_w = budget["ion_acceleration_w"]
        self.rf_retarding_w = budget["electron_retarding_sheath_work_w"]
        self.rf_total_w = _finite(budget["total_absorbed_w"], "RF total absorption", inclusive=True)
        self.rf_history.append({"time_s": t, "electron_density_m3": state["ne"], "electron_temperature_ev": state["te"],
            "electron_heating_w": self.rf_electron_w, "ion_acceleration_power_w": self.rf_ion_w,
            "secondary_electron_yield": self.rf_secondary_yield,
            "electron_retarding_sheath_work_w": self.rf_retarding_w, "total_absorbed_w": self.rf_total_w,
            "power_ledger": budget,
            "cycle_converged": bool(result.get("converged", False))})


def execute_global_transient(document: dict[str, Any], analysis: dict[str, Any],
                             rf_solver: Callable | None = None) -> dict[str, Any]:
    settings = dict(analysis.get("settings", {}))
    model = GlobalTransientModel(settings)
    stop = _finite(settings.get("stop_time_s", .001), "stop_time_s")
    if stop > 10:
        raise ValueError("stop_time_s must be ≤10 s")
    point_value = settings.get("output_points", 201)
    points = int(point_value)
    if isinstance(point_value, bool) or points != float(point_value) or not 3 <= points <= 5001:
        raise ValueError("output_points must be an integer 3–5001")
    pulse = PowerPulse.parse(settings)
    bounds = [0, stop]+pulse.breakpoints(stop)
    refresh = _finite(settings.get("rf_update_interval_s", stop/20), "rf_update_interval_s")
    if model.mode == "rf_coupled":
        from .plasma import CCPSettings
        carrier_settings = dict(settings) | {"pulse_frequency_hz": None, "pulse_duty_cycle": 1, "pulse_off_fraction": 1}
        carrier_frequency = CCPSettings.parse(carrier_settings).cycle_frequency_hz
        if math.ceil(stop/refresh) > 200:
            raise ValueError("At most 200 macro RF refreshes are permitted")
        if refresh*carrier_frequency < 10:
            raise ValueError("RF refresh intervals must span at least 10 carrier cycles")
        bounds.extend(float(t) for t in np.arange(refresh, stop, refresh) if t < stop-stop*1e-12)
        if rf_solver is None:
            from .plasma import solve_ccp
            rf_solver = solve_ccp
    sorted_bounds = sorted(set(bounds))
    bounds = [sorted_bounds[0]]
    for boundary in sorted_bounds[1:]:
        if boundary-bounds[-1] > stop*1e-12:
            bounds.append(boundary)
    if model.mode == "rf_coupled" and len(bounds)-1 > 200:
        raise ValueError("At most 200 combined RF refresh/pulse intervals are permitted")
    rtol = _finite(settings.get("macro_relative_tolerance", 1e-6), "macro_relative_tolerance")
    if not 1e-9 <= rtol <= 1e-3:
        raise ValueError("macro_relative_tolerance must be 1e-9–1e-3")
    max_step = _finite(settings.get("macro_max_step_s", stop/100), "macro_max_step_s")
    requested = np.linspace(0, stop, points)
    times, states = [0.0], [model.initial.copy()]
    current = model.initial.copy()
    solver_nfev = solver_njev = solver_nlu = 0
    termination, success, negative_correction = "completed_requested_time", True, 0.0
    lower, upper = model.temperature_domain

    def low_event(t, y): return model.unpack(y)[2]-lower
    def high_event(t, y): return upper-model.unpack(y)[2]
    def density_event(t, y): return model.unpack(y)[1]-1e10
    def gas_event(t, y): return y[model.count+1]-1
    def negative_event(t, y): return float(np.min(y[:model.count]))+1e-8
    def sheath_event(t, y):
        return model.evaluate(y, factor)["floating_sheath_potential_v"] if model.mode == "prescribed_absorbed" or factor == 0 else 1.0
    events = (low_event, high_event, density_event, gas_event, negative_event, sheath_event)
    for event in events:
        event.terminal, event.direction = True, -1
    for left, right in zip(bounds, bounds[1:]):
        factor = pulse.factor((left+right)/2)
        if model.mode == "rf_coupled" and factor > 0:
            assert rf_solver is not None
            model.update_rf(left, current, rf_solver)
        sample = requested[(requested > left+stop*1e-12) & (requested <= right+stop*1e-12)]
        # Include boundary state to pass the real integration result forward.
        sample = np.unique(np.clip(np.concatenate((sample, [right])), left, right))
        interval = right-left
        # Dimensionless interval time gives event root finding adequate absolute
        # accuracy even when fast electron heating reaches a fit bound in ps.
        interval_events = []
        for event in events:
            def interval_event(u, y, event=event): return event(left+u*interval, y)
            interval_event.terminal, interval_event.direction = True, -1
            interval_events.append(interval_event)
        fit = solve_ivp(lambda u, y: interval*model.rhs(left+u*interval, y, factor), (0, 1), current,
            method="BDF", t_eval=(sample-left)/interval, events=interval_events, rtol=rtol,
            atol=np.concatenate((np.full(model.count, 1e-10), [1e-10, 1e-7], np.full(7, 1e-10))),
            max_step=min(max_step/interval, 1))
        solver_nfev += fit.nfev
        solver_njev += fit.njev
        solver_nlu += fit.nlu
        for t, y in zip(fit.t, fit.y.T if len(fit.t) else []):
            negative_correction = max(negative_correction, float(np.max(np.maximum(-y[:model.count], 0))))
            times.append(float(left+t*interval)); states.append(y.copy())
        if fit.status == 1:
            index = next(i for i, samples in enumerate(fit.t_events) if len(samples))
            t, current = float(left+fit.t_events[index][-1]*interval), fit.y_events[index][-1].copy()
            if not times or t > times[-1]+stop*1e-12:
                times.append(t); states.append(current)
            termination = ("electron_temperature_below_fit_domain", "electron_temperature_above_fit_domain",
                           "electron_density_below_quasineutral_floor", "gas_temperature_below_1_K",
                           "negative_species_density_exceeded_tolerance", "floating_sheath_potential_became_negative")[index]
            success = False
            break
        if not fit.success:
            termination, success = "integrator_failed: "+fit.message, False
            break
        current = fit.y[:, -1].copy()
        minimum = float(np.min(current[:model.count]))
        if minimum < -1e-8:
            termination, success = "negative_species_density_exceeded_tolerance", False
            break
        correction = np.maximum(-current[:model.count], 0)
        negative_correction = max(negative_correction, float(np.max(correction)))
        current[:model.count] = np.maximum(current[:model.count], 0)
    # Sample values are actual solver states. Pulse powers are reported with
    # each interval's input; RF refresh history provides the sample/hold inputs.
    observations = []
    for t, y in zip(times, states):
        if model.mode == "rf_coupled" and model.rf_history:
            reference = next((row for row in reversed(model.rf_history) if row["time_s"] <= t), model.rf_history[0])
            model.rf_electron_w, model.rf_ion_w = reference["electron_heating_w"], reference["ion_acceleration_power_w"]
            model.rf_secondary_yield = reference["secondary_electron_yield"]
            model.rf_retarding_w, model.rf_total_w = reference["electron_retarding_sheath_work_w"], reference["total_absorbed_w"]
        observations.append(model.evaluate(y, pulse.factor(t)))
    final = observations[-1]
    y_final = states[-1]
    integrals = y_final[model.count+2:]
    electron_change = (y_final[model.count]-model.initial[model.count])*model.energy_scale
    gas_change = model.capacity*(y_final[model.count+1]-model.initial[model.count+1])
    residual_e = electron_change-(integrals[0]-integrals[1])
    residual_g = gas_change-(integrals[2]-integrals[3])
    residual_total = electron_change+gas_change-(integrals[4]-integrals[6])
    signals = [{"name": f"n({name})", "unit": "m⁻³", "values": [float(max(o["density"][i], 0)) for o in observations]}
               for i, name in enumerate(model.names)]
    power_source_name = "P(electron_heating)" if model.mode == "rf_coupled" else "P(total_plasma_power_source)"
    for name, key, unit in [("n(e)", "ne", "m⁻³"), ("Te", "te", "eV"), ("Tg", "tg", "K"),
        ("P(total_absorbed)", "total_input_w", "W"), (power_source_name, "electron_input_w", "W"),
        ("P(electron_loss)", "electron_loss_w", "W"), ("P(gas_heating)", "gas_input_w", "W"),
        ("P(ion_acceleration)", "ion_acceleration_w", "W"),
        ("P(electron_retarding_sheath_work)", "electron_retarding_sheath_work_w", "W"),
        ("P(gas_sink)", "gas_sink_w", "W"), ("pressure", "pressure_pa", "Pa")]:
        signals.append({"name": name, "unit": unit, "values": [float(o[key]) for o in observations]})
    rf_converged = all(row["cycle_converged"] for row in model.rf_history)
    metadata = {
        "name": "Time-dependent 0-D particle/electron-energy/gas-heat model", "version": "global-transient-0.1",
        "status": "reduced_model_unvalidated", "gas": model.gas, "power_mode": model.mode,
        "initialization": model.initialization, "temperature_domain_ev": list(model.temperature_domain),
        "coefficient_domain": "Terminal events stop at bounds; no published rate extrapolation. O₂ k20 has exclusive 1–4.5 eV bounds.",
        "time_scales": {"rf_period_s": 1/(carrier_frequency if model.mode == "rf_coupled" else float(settings.get("frequency_hz", 40e6))),
            "rf_update_interval_s": refresh if model.mode == "rf_coupled" else None,
            "neutral_residence_time_s": 1/model.pump,
            "gas_wall_thermal_time_s": model.capacity/model.conductance if model.conductance else None,
            "gas_flow_thermal_time_s": model.capacity/(model.feed*model.cp)},
        "assumptions": {"gas_heat_capacity_j_k": model.capacity, "heat_capacity_source": "user assumption" if "gas_heat_capacity_j_k" in settings else "constant ideal-gas Cv times initial neutral inventory; Ar 3/2 kB, O₂ 5/2 kB",
            "initial_gas_temperature_k": model.initial_tg, "background_gas_temperature_k": model.background_tg,
            "feed_temperature_k": model.feed_tg, "gas_wall_conductance_w_k": model.conductance,
            "wall_conductance_source": "user assumption (zero when omitted)",
            "gas_inelastic_heating_fraction": model.inelastic_fraction, "gas_ion_heating_fraction": model.ion_fraction,
            "energy_partition_source": "user assumptions; elastic transfers to gas once, other fractions zero when omitted",
            "flow_enthalpy": "feed particles/s * pure-feed ideal-gas Cp * (Tg-Tfeed); composition enthalpy and chemical enthalpy omitted",
            "pressure": "initial neutral pressure; subsequent pressure derived from evolving neutral densities and Tg; pumping rate held fixed",
            "RF": "actual cycle-average circuit powers refreshed at macro/pulse boundaries and held until next refresh; envelope scales averaged POWER, not RF carrier voltage",
            "secondary_electrons": "RF mode: thermalized secondaries add RF electron heating and thermal escape 2Te*(1+yield)*ion wall flux; prescribed mode retains source floating-wall closure without secondary emission",
            "RF_power_budget": "External total=electron heating+conductive sheath power-secondary transfer. Electron loss includes ion acceleration+secondary transfer-conductive sheath power (signed retarding work). RF off: phi satisfies collected=(1+yield)*ion flux; gross retarding=(1+yield)*Pi, internal secondary heating=yield*Pi, external input=0. Net ambipolar loss Pi comes from stored electron energy.",
            "quasineutrality": "ne = positive ion charge minus negative ion charge; electrons not transported independently",
            "electron_energy": "d(3/2 ne e Te V)/dt: RF uses electron heating minus collisional/electron-wall/Bohm losses; prescribed TOTAL plasma source subtracts these plus floating-sheath ion acceleration. Prescribed total source is not measured electron heating. Includes changing ne.",
            "particle_nonnegative": "scaled physical states; nonnegative RHS boundary and tiny accepted clipping; corrections reported"},
        "limitations": ["Populated plasma evolution; no ignition, extinction kinetics below fit domain, PIC, EEDF or spatial transport",
            "O₂ uses 48 particle reactions and a declared reduced ground-target energy closure, not 48 reaction energies" if model.gas == "O2" else "Ar ground-state ionization and lumped excitation; no metastable populations",
            "RF sample/hold approximation requires convergence checks with smaller rf_update_interval_s",
            "Constant heat capacity and pure-feed enthalpy approximation; no wall temperature evolution or surface reaction enthalpy"]}
    if model.gas == "O2":
        metadata["chemistry"] = study_metadata(oxygen_inputs(settings | {"gas_temperature_k": final["tg"]}, model.power_w))
    if final["electron_transport"] is not None:
        metadata["electron_transport"] = final["electron_transport"]
        metadata["chemistry_rate_distribution"] = "Published Maxwellian reaction fits at evolved Te; importing a transport EEDF does not replace chemistry rates"
    return {"kind": "global_transient", "converged": bool(success and rf_converged),
        "summary": {"electron_density_m3": final["ne"], "electron_temperature_ev": final["te"],
            "gas_temperature_k": final["tg"], "pressure_pa": final["pressure_pa"],
            "integrated_time_s": times[-1], "total_absorbed_energy_j": float(integrals[4]),
            "electron_energy_change_j": electron_change, "gas_energy_change_j": gas_change},
        "axis": {"name": "macro time", "unit": "s", "values": times}, "signals": signals,
        "tables": [{"name": "Macro energy conservation", "columns": ["balance", "residual_j"],
            "rows": [["electron", residual_e], ["gas", residual_g], ["total", residual_total]]},
            {"name": "Final species", "columns": ["species", "density_m3"],
             "rows": [[name, float(max(final["density"][i], 0))] for i, name in enumerate(model.names)]}],
        "diagnostics": {"termination": termination, "integration_success": success,
            "solver_evaluations": solver_nfev, "jacobian_evaluations": solver_njev, "lu_decompositions": solver_nlu,
            "electron_energy_residual_j": residual_e, "gas_energy_residual_j": residual_g, "total_energy_residual_j": residual_total,
            "max_atom_source_relative_error": max(abs(o["atom_source_relative_error"]) for o in observations),
            "max_negative_density_correction_in_scaled_state": negative_correction,
            "event_localization_boundary_trials": model.domain_trials, "rf_refreshes": model.rf_history,
            "pulse": {"frequency_hz": pulse.frequency_hz, "duty_cycle": pulse.duty_cycle, "off_fraction": pulse.off_fraction}},
        "solver": {"global": "scipy.integrate.solve_ivp", "method": "BDF", "relative_tolerance": rtol},
        "netlist": "", "model_metadata": metadata,
        "logs": ["Actual BDF time integration of species, electron energy and gas heat balance; RF cycles are averaged.",
            "Fit-domain termination: "+termination if not success else "Integrated to requested macro time."]}

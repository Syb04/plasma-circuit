"""Versioned 0-D particle/energy models, with explicit units and provenance.

No gas label is used as a substitute for a chemistry model.  Ar has a small
Maxwellian benchmark; molecular gases require an explicit reaction dataset.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

ELEMENTARY_CHARGE = 1.602176634e-19
ELECTRON_MASS = 9.1093837139e-31
ATOMIC_MASS = 1.66053906892e-27
BOLTZMANN = 1.380649e-23
EPSILON_0 = 8.8541878128e-12

ARGON_MODEL_VERSION = "ar-maxwellian-ground-state-0.1"
ARGON_REFERENCE = {
    "title": "Principles of Plasma Discharges and Materials Processing, 2nd edition",
    "authors": "M. A. Lieberman and A. J. Lichtenberg",
    "year": 2005,
    "url": "https://doi.org/10.1002/0471724254",
    "description": "Ar rate fits matched against the published reaction table below; its reference 1 cites this textbook, pp. 350–351. The textbook pages have not been directly inspected.",
    "coefficient_verification": "matched_published_reaction_table",
    "rate_table_source": {
        "title": "Three-dimensional fluid simulation of a planar coil inductively coupled argon plasma source for semiconductor processes",
        "authors": "Ming-Liang Zhao et al.",
        "year": 2024,
        "url": "https://doi.org/10.7498/aps.73.20240952",
        "location": "Table 1, reactions 2 and 3; coefficients m³/s, electron temperature eV",
        "checked_date": "2026-10-07",
    },
}


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label}は整数で指定してください")
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label}は整数で指定してください") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError(f"{label}は整数で指定してください")
    return int(number)


def argon_rates(temperature_ev: float) -> dict[str, float]:
    """Ground-state Ar fits, rate coefficients in m^3/s; Te in eV.

    This reduced model is intended for 1--7 eV; it excludes metastable and
    stepwise ionization, an electron-energy-distribution calculation, and
    experimentally calibrated surface chemistry.
    """
    t = float(temperature_ev)
    if not math.isfinite(t) or t <= 0:
        raise ValueError("電子温度は正の有限値で指定してください")
    return {
        "ionization": 2.34e-14 * t**0.59 * math.exp(-17.44 / t),
        "excitation": 2.48e-14 * t**0.33 * math.exp(-12.78 / t),
    }


@dataclass(frozen=True)
class RateTable:
    """Electron-temperature table, with no extrapolation.

    ``coefficients_m3_s`` is the legacy constructor name. ``units`` identifies
    the coefficients' actual units for new first- and third-order tables.
    """

    temperature_ev: tuple[float, ...]
    coefficients_m3_s: tuple[float, ...]
    units: str = "m^3/s"

    @classmethod
    def from_dict(cls, data: dict[str, Any], order: int = 2) -> "RateTable":
        if "coefficients" in data and "units" not in data:
            raise ValueError("coefficientsの反応係数表にはunitsの単位を明示してください")
        units = str(data.get("units", "m^3/s"))
        expected_units = {1: "s^-1", 2: "m^3/s", 3: "m^6/s"}.get(order)
        if units != expected_units:
            raise ValueError(f"{order}体反応の速度係数の単位には{expected_units}を指定してください")
        if "coefficients" in data and "coefficients_m3_s" in data:
            raise ValueError("反応係数表のcoefficientsとcoefficients_m3_sを同時に指定しないでください")
        if "coefficients_m3_s" in data and order != 2:
            raise ValueError("coefficients_m3_sは二体反応専用です")
        ts = tuple(float(v) for v in data["temperature_ev"])
        ks = tuple(float(v) for v in data.get("coefficients", data.get("coefficients_m3_s", ())))
        if len(ts) < 2 or len(ts) != len(ks):
            raise ValueError("反応係数表には同じ長さの温度・係数を2点以上指定してください")
        if any(not math.isfinite(v) or v <= 0 for v in ts):
            raise ValueError("反応係数表の温度は正の有限値で指定してください")
        if any(b <= a for a, b in zip(ts, ts[1:])):
            raise ValueError("反応係数表の温度は昇順にしてください")
        if any(not math.isfinite(v) or v < 0 for v in ks):
            raise ValueError("反応係数は0以上の有限値で指定してください")
        return cls(ts, ks, units)

    @property
    def coefficients(self) -> tuple[float, ...]:
        return self.coefficients_m3_s

    def evaluate(self, temperature_ev: float) -> float:
        t = float(temperature_ev)
        if not math.isfinite(t) or t <= 0:
            raise ValueError("電子温度は正の有限値で指定してください")
        if t < self.temperature_ev[0] or t > self.temperature_ev[-1]:
            raise ValueError(
                f"電子温度 {t:.4g} eV は反応係数表の適用範囲 "
                f"{self.temperature_ev[0]:g}–{self.temperature_ev[-1]:g} eV 外です"
            )
        # Log interpolation for strictly positive rates; linear for a table
        # containing zeros, which are meaningful thresholds.
        if min(self.coefficients_m3_s) > 0:
            return float(np.exp(np.interp(t, self.temperature_ev, np.log(self.coefficients_m3_s))))
        return float(np.interp(t, self.temperature_ev, self.coefficients_m3_s))


@dataclass(frozen=True)
class ConstantRate:
    """Explicit first-, second-, or third-order coefficient in SI units.

    This coefficient is independent of electron temperature. Any gas-temperature
    dependence must already have been evaluated at the dataset's stated Tg.
    """

    coefficient: float
    units: str

    @classmethod
    def from_dict(cls, data: dict[str, Any], order: int) -> "ConstantRate":
        units = str(data.get("units", ""))
        expected_units = {1: "s^-1", 2: "m^3/s", 3: "m^6/s"}.get(order)
        if units != expected_units:
            raise ValueError(f"{order}体反応の速度係数の単位には{expected_units}を指定してください")
        coefficient = float(data["coefficient"])
        if not math.isfinite(coefficient) or coefficient < 0:
            raise ValueError("定数反応係数は0以上の有限値で指定してください")
        if any(key in data for key in ("temperature_ev", "coefficients", "coefficients_m3_s")):
            raise ValueError("定数反応係数と温度表を同時に指定しないでください")
        return cls(coefficient, units)

    def evaluate(self, temperature_ev: float) -> float:
        if not math.isfinite(float(temperature_ev)) or float(temperature_ev) <= 0:
            raise ValueError("電子温度は正の有限値で指定してください")
        return self.coefficient


@dataclass(frozen=True)
class Species:
    name: str
    charge: int
    mass_amu: float
    reservoir: bool = False
    wall_loss_s: float = 0.0
    wall_loss_model: str = "constant"
    elements: dict[str, int] = field(default_factory=dict)
    wall_products: dict[str, float] = field(default_factory=dict)
    wall_source: str = ""


@dataclass(frozen=True)
class Reaction:
    name: str
    reactants: dict[str, int]
    products: dict[str, int]
    rate: RateTable | ConstantRate
    electron_energy_loss_ev: float
    source: str

    @property
    def order(self) -> int:
        return sum(self.reactants.values())


class ReactionNetwork:
    """User-supplied first-/second-/third-order, single-gas chemistry.

    Electrons have the reserved name ``e``.  All other species are declared.
    Charge is checked at import, and each coefficient must include a source.
    Declared elemental composition is checked for all gas reactions and
    declared wall conversion products.
    A neutral reservoir is held constant at the requested gas density; other
    species evolve. Wall loss constants are in s^-1; wall product yields are
    numbers of returning particles per wall loss event, including fractions.
    Electron energy losses in eV/event are signed: negative means electron
    heating, as in a superelastic collision.
    """

    def __init__(self, data: dict[str, Any], gas: str):
        if data.get("gas") != gas:
            raise ValueError("反応モデルのガスと解析ガスが一致しません")
        self.gas = gas
        self.name = str(data.get("name", ""))
        self.version = str(data.get("version", ""))
        self.source = str(data.get("source", ""))
        if not all((self.name, self.version, self.source)):
            raise ValueError("反応モデルにはname・version・sourceが必要です")
        self.species: dict[str, Species] = {}
        species_rows = data.get("species", [])
        self.element_conservation_checked = any("elements" in row for row in species_rows)
        for row in species_rows:
            name = str(row["name"])
            if name == "e" or name in self.species or not name:
                raise ValueError("粒子種名は重複せず、eは電子専用です")
            charge = _integer(row.get("charge", 0), "電荷数")
            if charge not in {-1, 0, 1}:
                raise ValueError("初期版は単価の正・負イオン（電荷数-1、0、1）に対応します")
            mass = float(row["mass_amu"])
            wall = float(row.get("wall_loss_s", 0.0))
            if mass <= 0 or wall < 0 or not all(map(math.isfinite, (mass, wall))):
                raise ValueError("粒子質量・壁損失係数を確認してください")
            wall_model = str(row.get("wall_loss_model", "bohm" if charge > 0 else "constant"))
            if wall_model not in {"constant", "bohm"} or (wall_model == "bohm" and charge != 1):
                raise ValueError("wall_loss_modelはconstantまたは正イオンのbohmを指定してください")
            elements = {str(n): _integer(c, "元素数") for n, c in row.get("elements", {}).items()}
            if self.element_conservation_checked and not elements:
                raise ValueError("元素数を宣言する場合は全粒子種にelementsを指定してください")
            if any(not n.strip() or c <= 0 for n, c in elements.items()):
                raise ValueError("元素名と正の整数の元素数を指定してください")
            wall_products = {str(n): float(c) for n, c in row.get("wall_products", {}).items()}
            if any(not math.isfinite(c) or c <= 0 for c in wall_products.values()):
                raise ValueError("壁生成物の生成数は正の有限値で指定してください")
            wall_source = str(row.get("wall_source", "")).strip()
            if "wall_products" in row and not wall_source:
                raise ValueError("壁生成物を指定する場合はwall_sourceの出典が必要です")
            self.species[name] = Species(name, charge, mass, bool(row.get("reservoir", False)),
                                         wall, wall_model, elements, wall_products, wall_source)
        if not self.species or len(self.species) > 30:
            raise ValueError("粒子種は1〜30種で指定してください")
        reservoirs = [s for s in self.species.values() if s.reservoir]
        if len(reservoirs) != 1 or reservoirs[0].charge != 0 or reservoirs[0].name != gas:
            raise ValueError("指定した単独ガスを唯一の中性リザーバーにしてください")
        self.dynamic = [s.name for s in self.species.values() if not s.reservoir]
        for species in self.species.values():
            if any(n not in self.species for n in species.wall_products):
                raise ValueError("壁生成物に未定義の粒子種があります（電子は電荷閉包で扱います）")
            if species.reservoir and species.wall_products:
                raise ValueError("リザーバーには壁生成物を指定できません")
            if species.wall_products:
                self._check_elements({species.name: 1}, species.wall_products, f"壁反応 {species.name}")
        self.reactions: list[Reaction] = []
        charges = {name: s.charge for name, s in self.species.items()} | {"e": -1}
        for row in data.get("reactions", []):
            reactants = {str(n): _integer(c, "反応の化学量論係数") for n, c in row["reactants"].items()}
            products = {str(n): _integer(c, "反応の化学量論係数") for n, c in row["products"].items()}
            if any(n not in charges for n in reactants | products):
                raise ValueError("反応式に未定義の粒子種があります")
            if any(c <= 0 for c in list(reactants.values()) + list(products.values())):
                raise ValueError("反応の化学量論係数は正の整数で指定してください")
            order = sum(reactants.values())
            if order not in {1, 2, 3}:
                raise ValueError("気相反応の次数は1・2・3に対応します（単位s^-1・m^3/s・m^6/s）")
            before = sum(charges[n] * c for n, c in reactants.items())
            after = sum(charges[n] * c for n, c in products.items())
            if before != after:
                raise ValueError(f"反応 {row.get('name', '')} は電荷を保存していません")
            self._check_elements(reactants, products, f"反応 {row.get('name', '')}")
            source = str(row.get("source", "")).strip()
            loss = float(row.get("electron_energy_loss_ev", 0))
            if not source or not math.isfinite(loss):
                raise ValueError("反応ごとの出典と有限値の電子エネルギー損失（eV/event）が必要です")
            rate_data = row["rate"]
            rate_type = rate_data.get("type", "table")
            if rate_type not in {"table", "constant"}:
                raise ValueError("反応係数のtypeはtableまたはconstantを指定してください")
            rate = (ConstantRate.from_dict(rate_data, order) if rate_type == "constant"
                    else RateTable.from_dict(rate_data, order))
            self.reactions.append(Reaction(str(row.get("name", "")), reactants, products,
                                           rate, loss, source))
        if not self.reactions or len(self.reactions) > 200:
            raise ValueError("反応は1〜200件で指定してください")
        if not any(s.charge > 0 for s in self.species.values()):
            raise ValueError("グローバルモデルには正イオンが必要です")
        if gas in {"O2", "CF4"}:
            if not any(s.charge < 0 for s in self.species.values()):
                raise ValueError("O₂・CF₄モデルには負イオンを含めてください")
            if not any(s.charge == 0 and not s.reservoir for s in self.species.values()):
                raise ValueError("O₂・CF₄モデルには解離生成物を含めてください")
        tables = [r.rate for r in self.reactions if isinstance(r.rate, RateTable)]
        ranges = [(table.temperature_ev[0], table.temperature_ev[-1]) for table in tables]
        if "temperature_range_ev" in data:
            declared_range = tuple(float(t) for t in data["temperature_range_ev"])
            if len(declared_range) != 2 or any(not math.isfinite(t) or t <= 0 for t in declared_range):
                raise ValueError("temperature_range_evには正の有限値の下限・上限を指定してください")
            ranges.append(declared_range)
        if not ranges:
            raise ValueError("定数反応のみのモデルにはtemperature_range_evが必要です")
        self.min_temperature = max(lower for lower, _ in ranges)
        self.max_temperature = min(upper for _, upper in ranges)
        if self.min_temperature >= self.max_temperature:
            raise ValueError("全反応の係数表・モデルに共通の温度範囲が必要です")

    def _check_elements(self, reactants: dict[str, int | float],
                        products: dict[str, int | float], label: str) -> None:
        if not self.element_conservation_checked:
            return
        elements = {element for species in self.species.values() for element in species.elements}
        for element in elements:
            before = sum(self.species[n].elements.get(element, 0)*c
                         for n, c in reactants.items() if n != "e")
            after = sum(self.species[n].elements.get(element, 0)*c
                        for n, c in products.items() if n != "e")
            if not math.isclose(before, after, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError(f"{label} は元素 {element} の数を保存していません")

    def _densities(self, densities: dict[str, float]) -> dict[str, float]:
        checked = {name: float(densities.get(name, 0.0)) for name in self.species}
        if any(not math.isfinite(n) or n < 0 for n in checked.values()):
            raise ValueError("粒子密度は0以上の有限値（m^-3）で指定してください")
        return checked

    def quasineutral_electron_density(self, densities: dict[str, float]) -> float:
        return sum(s.charge * densities.get(s.name, 0.0) for s in self.species.values())

    def source_terms(self, densities: dict[str, float], temperature_ev: float,
                     wall_rates: dict[str, float] | None = None) -> tuple[dict[str, float], float]:
        """Dynamic heavy-particle sources in m^-3/s and electron loss in W/m^3."""
        gas_terms, electron_loss_w_m3 = self.gas_source_terms(densities, temperature_ev)
        wall_terms = self.wall_source_terms(densities, wall_rates)
        return {name: gas_terms[name] + wall_terms[name] for name in self.dynamic}, electron_loss_w_m3

    def gas_source_terms(self, densities: dict[str, float], temperature_ev: float
                         ) -> tuple[dict[str, float], float]:
        """Gas sources for all species, including e and the held reservoir.

        Reservoir terms are diagnostic exchanges; the reservoir is held fixed by
        ``source_terms``. Event rates are k times reactant density powers, with
        the published coefficient already defining identical-particle factors.
        """
        checked = self._densities(densities)
        electron_density = self.quasineutral_electron_density(checked)
        if electron_density <= 0:
            raise ValueError("準中性条件から求めた電子密度が0以下です。初期粒子密度を確認してください")
        t = float(temperature_ev)
        if not math.isfinite(t) or t <= 0:
            raise ValueError("電子温度は正の有限値で指定してください")
        if not self.min_temperature <= t <= self.max_temperature:
            raise ValueError(f"電子温度 {t:g} eV は反応モデルの適用範囲外です")
        all_densities = checked | {"e": electron_density}
        terms = {name: 0.0 for name in all_densities}
        electron_loss_w_m3 = 0.0
        for reaction in self.reactions:
            event_rate = reaction.rate.evaluate(t)
            for name, count in reaction.reactants.items():
                event_rate *= all_densities[name]**count
            for name in terms:
                terms[name] += (reaction.products.get(name, 0) - reaction.reactants.get(name, 0)) * event_rate
            electron_loss_w_m3 += event_rate * reaction.electron_energy_loss_ev * ELEMENTARY_CHARGE
        return terms, electron_loss_w_m3

    def wall_source_terms(self, densities: dict[str, float],
                          wall_rates: dict[str, float] | None = None) -> dict[str, float]:
        """Wall-only sources in m^-3/s, including returned reservoir particles.

        The e term equals the net heavy-species charge source: it is the
        quasineutral electron closure, not an independent electron transport
        model. Positive/negative ion wall losses require electron loss/injection.
        """
        checked = self._densities(densities)
        terms = {name: 0.0 for name in self.species}
        for name in self.dynamic:
            species = self.species[name]
            wall_rate = float(wall_rates.get(name, species.wall_loss_s) if wall_rates is not None else species.wall_loss_s)
            if not math.isfinite(wall_rate) or wall_rate < 0:
                raise ValueError("壁損失係数は0以上の有限値（s^-1）で指定してください")
            event_rate = wall_rate * checked[name]
            terms[name] -= event_rate
            for product, count in species.wall_products.items():
                terms[product] += count * event_rate
        terms["e"] = sum(species.charge*terms[species.name] for species in self.species.values())
        return terms

    def metadata(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "gas": self.gas,
                "source": self.source, "status": "user_supplied_unvalidated",
                "temperature_range_ev": [self.min_temperature, self.max_temperature],
                "electron_distribution": "Maxwellian / supplied Te-dependent or constant rates",
                "element_conservation_checked": self.element_conservation_checked,
                "element_conservation_scope": "Gas reactions and declared wall products; unreturned wall losses leave the volume. Held reservoir sources are diagnostic exchanges.",
                "electron_energy_loss_units": "eV/event; signed, negative means electron heating",
                "wall_loss": "Positive ions: Bohm flux by default, or explicit constant s^-1; other species: explicit constants s^-1. Declared wall products return to the volume. Electron loss closes net wall charge balance.",
                "species": [s.__dict__ for s in self.species.values()],
                "reaction_sources": [{"name": r.name, "source": r.source, "order": r.order,
                                      "coefficient_units": r.rate.units} for r in self.reactions]}

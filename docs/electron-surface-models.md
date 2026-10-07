# Electron transport, independent surfaces, and sheath electron heating

These optional helpers make their calculations and assumptions explicit. They
do not contain a validated silicon surface dataset, a default electron
cross-section set, or a Boltzmann/PIC solution. Existing explicit collision
frequency and oxygen-study surface assumptions remain selectable.

## Electron rate integration

`backend/app/electron_transport.py` computes

\[
k=\int \sigma(E)\sqrt{2eE/m_e}\,f(E)\,dE,\qquad
\nu_m=\sum_t n_t k_{m,t}.
\]

The cross-section energy axis is eV, cross sections are m², and `f(E)` is an
energy **probability density** in eV⁻¹. It is not an EEPF. The output `k` is
m³/s and `nu` is a per-electron frequency in s⁻¹. Neutral density is multiplied
once. The Maxwellian energy PDF is
`2/sqrt(pi) * sqrt(E)/Te**1.5 * exp(-E/Te)` and has mean energy `1.5*Te`.

`electron_transport.mode` accepts `explicit_nu` (default) or
`cross_section_eedf`. Imported data have this structure:

```json
{
  "mode": "cross_section_eedf",
  "cross_sections": [
    {
      "id": "O2_momentum",
      "target": "O2",
      "process": "momentum_transfer",
      "energy_ev": [0, 300],
      "sigma_m2": [2e-20, 2e-20],
      "source": {"title": "Synthetic constant-sigma demonstration; replace with sourced physical data"}
    }
  ],
  "eedf": {"mode": "maxwellian"},
  "maxwell_tail_probability_tolerance": 1e-6
}
```

The displayed constant cross section is an analytic test fixture, not an O₂
measurement or a recommended model input. Use measured or published data with
a source title, citation, URL, or DOI. One total momentum-transfer table is
required for each nonzero neutral target. Extra process tables return actual
rate integrals keyed by `id`; mapping them into reactions is an explicit
chemistry responsibility. They do not silently overwrite a network's Maxwellian
rate fits.

A supplied EEDF uses
`{"mode":"tabulated","energy_ev":[...],"probability_per_ev":[...],"source":...}`.
The piecewise-linear PDF must integrate to one within 0.001 by default; accepted
rounding error is normalized and reported. A tolerance above 0.01 is rejected.
The imported EEDF's finite support is explicit; it is zero outside that support.

Tables must have strictly increasing nonnegative energy, equal lengths, and
finite nonnegative values. Cross sections are piecewise linear. A source is
mandatory. No cross-section extrapolation is performed. The complete tabulated
EEDF support must be covered. For a Maxwellian, data must start at zero unless a
covered reaction threshold explicitly declares a zero cross section below it.
The upper omitted Maxwellian probability must be below the requested tolerance
(maximum 0.01) and is reported. This probability bound **does not bound the
unknown cross-section contribution to the omitted rate tail**. The tail is not
renormalized into the supplied range.

The isotropic-Maxwellian/imported-EEDF integration assumes stationary neutrals.
It does not solve nonlocal electron kinetics, anisotropic distributions, or the
RF Boltzmann equation. A non-Maxwellian momentum calculation combined with
Maxwellian chemistry fits is a disclosed hybrid assumption, not a consistent
kinetic chemistry model.

## Independent electrode and wall parameters

`backend/app/surface_models.py` requires a separate `surface_parameters` entry
for `cathode`, `anode`, and `wall` once the feature is configured. Each entry has
`material`, `state`, `temperature_k`, `gamma_o`, `gamma_metastable`, and
`secondary_electron_yield`. `gamma_metastable` may be a scalar applying to both
`O2(a1Delta)` and `O2(b1Sigma)`, or an object with both species keys. All
probabilities/yields in this reduced model are finite and in [0,1].

`provenance` maps coefficient names to a user-assumption description or citation.
Absent provenance is recorded explicitly as a user input, never as a material
measurement. `ranges` maps names to `[lower,upper]`; the nominal value must lie
inside. A scalar metastable range applies to both states; a species-keyed range
keeps them separate. Omitted ranges are degenerate `[nominal,nominal]` bounds,
not invented uncertainty. The surface temperature and conditioning identify the
supplied coefficient; no Arrhenius or temperature law is inferred.

The helper takes geometric areas independently of the coefficient data. The
well-mixed reaction-limited neutral loss coefficient is

\[
k_{w,s}=\frac{\bar v_s}{4V}\sum_j A_j\gamma_{s,j},
\quad \bar v_s=\sqrt{8k_BT_g/(\pi m_s)}.
\]

It returns coefficients in s⁻¹, each surface's contribution, bounds propagated
from the provided ranges, and exact sensitivities
`dk/dgamma_j = vbar*A_j/(4*V)`. Ground and excited atomic oxygen use `gamma_o`;
each oxygen-molecule metastable uses its supplied quenching probability. Gas
temperature determines the incident neutral speed; surface temperature does not
replace it. A uniform probability reduces to the familiar `gamma*vbar*A/(4V)`.
Zero area or zero probability contributes zero loss.

`effective_parameters` reports simple area-weighted probabilities.
`effective_boundary_parameters` instead averages `b=gamma/(2-gamma)` over area
and returns `gamma_eff=2*b/(1+b)`, for compatibility with an existing
diffusion-plus-surface-resistance formula. The reported reaction-limited
coefficients do not add a diffusion bottleneck. An implementation using the
oxygen study's diffusion closure must use that closure once, rather than adding
the reported kinetic coefficients as a second independent wall sink.

Ion-induced secondary emission uses the **local electrode yield**, not its area
average. The RF conductive sheath must count conventional current
`-(1+gamma_j)*e*Gamma_i*A_j` and secondary acceleration power
`gamma_j*e*Gamma_i*A_j*mean(max(Vplasma-Vmetal,0))`. These emitted electrons enter
the electron-energy channel once. A neutral wall yield alone is insufficient
to calculate wall secondary heating without a local incident ion flux and
sheath drop.

The legacy oxygen study documents `gammaO=0.17` measured on stainless steel near
300 K and `gammaMeta=0.007` measured on Fe and adopted in its SUS case. Those
values are not silicon coefficients. A silicon material name never fills in
missing coefficients. Surface names, temperatures, and state descriptions do
not themselves create a kinetic model.

## Reduced moving-sheath heating

`backend/app/electron_heating.py` accepts `bulk_drude` (default) and
`moving_wall_maxwellian`. Both compute the actual bulk
`mean(Rbulk*Ibulk**2)` on the RF time mesh. A separately supplied secondary
acceleration channel is added once. Capacitor `V*dQ/dt` is never assigned to
electron heating.

The optional moving-wall closure uses a supplied sheath-position waveform, or
the actual RF voltage waveform with the existing cold uniform-ion matrix
relation `s(t)=sqrt(2*eps0*max(Vplasma-Vmetal,0)/(e*ni))`. It differentiates that
position on the retained adaptive time mesh. This model assumes a planar,
specular reflecting boundary, constant incident Maxwellian electron density
and temperature, and a selectable `reflection_probability` in [0,1] (default 1).
It resolves boundary motion, not a spatial electron distribution.

For boundary speed `u>0` into the plasma, electrons with normal velocity `v<u`
encounter the sheath at rate `(u-v)*f(v)` and gain lab-frame energy
`2*me*u*(u-v)` on reflection. With `vt=sqrt(e*Te/me)` this gives total work

\[
P/A=2m_en_er u[(u^2+v_t^2)\Phi(u/v_t)+uv_t\phi(u/v_t)].
\]

Subtract the reversible constant-pressure work `ne*e*Te*r*u`. The remaining
nonnegative reflection work is the returned moving-wall electron-heating
estimate. Constant-pressure work integrates exactly from endpoint displacement
and is reported separately; it vanishes for periodic motion. In the slow-wall
limit, irreversible heating is proportional to `u²`; a stationary sheath or
zero reflection gives zero heating. Fast sheath motion, electron-attracting
voltage, or nonperiodic retained motion produces applicability diagnostics.

The incoming reservoir assumption omits electron depletion, phase mixing,
nonlocal transport, and secondary-electron avalanche. The closure needs kinetic
validation and is not a quantitatively established low-pressure CCP prediction.

## RF energy ledger and optional backreaction

A standalone moving-wall estimate does not extract power from the original RF
circuit. The helper therefore reports the original port/bulk/conductive-sheath
ledger and the extended ledger. Its extra heating changes the RF residual;
power is never silently rescaled. The caller can also supply an independently
defined electron-available RF power after other channels have been removed.

For an effective-resistance closure, an outer RF iteration may set
`Rheat=P_moving_estimate/mean(Ibulk**2)`, solve the actual RF circuit with that
resistance, and repeat until the estimate and measured resistor power agree.
When `effective_circuit_backreaction=true`, the helper requires
`circuit_sheath_heating_power_w`, uses that measured power once, and reports its
difference from the waveform estimate. Failure to converge is flagged. This is
an explicit reduced RF energy closure, not a fitted material coefficient or a
replacement for kinetic sheath modeling.

Stamped secondary acceleration is already part of the conductive-sheath
electrical power. The ledger subtracts that channel from conductive-sheath power
when adding it to electron heating, avoiding double counting. Signed conductive
channels are kept signed; their sum is not forced to equal ion acceleration
alone. Budgets outside the configurable relative tolerance (default 1%) are
reported as invalid.

## Verification

The focused tests independently verify a constant-cross-section Maxwellian
rate `k=sigma*sqrt(8*e*Te/(pi*me))`, a uniform finite-support EEDF integral, SI
neutral-density scaling, source/normalization/coverage rejection, area-weighted
losses and derivative sensitivities, the Maxwellian reflection-energy integral,
stationary/nonreflecting limits, slow-motion quadratic heating, reversible-work
separation, and secondary/backreaction energy accounting. The supplied numerical
fixtures are analytic examples, not imported physical datasets.

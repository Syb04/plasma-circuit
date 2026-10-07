# Ion energy and radial circuit models

These are explicit reduced models with analytic and numerical checks. They have not been validated against an experiment, PIC simulation, or a full electromagnetic reactor model. A successful numerical solve and a passing model-domain screen are separate results.

## IEDF trajectory calculation

`app.ion_transport.solve_iedf(waveform, settings, species=None)` consumes the actual prescribed cathode sheath waveform. `waveform` contains increasing `time_s` and matching `voltage_v` arrays covering at least one complete RF period. Positive voltage is the plasma-to-electrode drop that accelerates a positive ion. The final full cycle is continued periodically; its endpoint mismatch is reported. Negative drops are clipped to zero and trigger a model-domain warning because an electron sheath is outside this approximation.

The sheath is a fixed planar slab with a uniform electric field:

\[
E(t)=\max(V_s(t),0)/s,\qquad \dot x=v,\qquad \dot v=zeE(t)/m_i.
\]

The user can specify `sheath_thickness_m`. Otherwise the fixed slab thickness is estimated from the mean accelerating drop and the supplied sheath-entrance charge density:

\[
s=\sqrt{2\epsilon_0\langle V_s\rangle/(e\sum_i z_i n_{i,edge})}.
\]

This is a mean-drop uniform-ion matrix thickness estimate, not a self-consistent instantaneous sheath edge. Particle motion does not alter the supplied field. The species enter at the electropositive Bohm speed `sqrt(z*e*Te/m)` with stratified uniform launch phases. Their entrance kinetic energy is `z*Te/2` eV. Entrance density is an explicit input; the function does not derive an electronegative Bohm correction.

The integrator freezes the field at the midpoint of each bounded substep and resolves boundary, turning, and charge-exchange events inside that substep. Its step is the smaller of an RF-period step and a mean-DC-transit step. Temporal refinement should be checked for the requested waveform, especially narrow pulses. In the constant-voltage collisionless limit, the event solution is analytic and conserves `energy = z*Vdc + z*Te/2` to floating-point accuracy. For RF excitation, the field acts over the entire transit, so an IEDF is not obtained by histogramming `e*Vs(t)`.

Optional charge exchange uses a **user-supplied constant cross section** and neutral density. An exponential path distance samples each collision; the old ion becomes a ballistic fast neutral and its replacement ion draws axial velocity from a neutral Maxwellian at `gas_temperature_k`. This is an equal-mass resonant approximation restricted to singly charged ions. It excludes energy-dependent cross sections, elastic scattering, angular trajectories, and subsequent neutral collisions. `neutral_density_m3` can be given directly; otherwise `pressure_pa/(kB*Tg)` supplies it. A zero cross section disables collisions. Gas labels do not select verified collision data.

An example configuration is:

```json
{
  "frequency_hz": 40000000,
  "electron_temperature_ev": 3,
  "pressure_pa": 1.333223684,
  "gas_temperature_k": 300,
  "particles_per_species": 512,
  "histogram_bins": 80,
  "steps_per_rf_period": 256,
  "steps_per_transit": 64,
  "max_transit_periods": 30,
  "seed": 17,
  "species": [{"name": "Ar+", "mass_amu": 39.948, "density_m3": 2000000000000000,
               "charge_number": 1, "charge_exchange_cross_section_m2": 0}]
}
```

For CCP/global integration, the wrapper accepts these controls under `settings.iedf` and supplies the calculated sheath waveform. The default species must use the model's **sheath-entrance** ion density, including its edge factor, rather than the bulk density. Supplied species densities take precedence.

The result has an energy axis, species PDFs in `eV^-1`, a flux-weighted combined PDF, optional fast-neutral PDFs, and a species table. `mean_ion_energy_ev` and `ion_energy_std_ev` describe the collected ion distribution; width means standard deviation. Each collected-species PDF integrates to one. Incident ion flux is `n_edge*uB*(arrived/launched)`; the charge-weighted current multiplies this by electrode area and charge. Neutral flux can exceed ion flux when an ion undergoes multiple charge-exchange events. Empty collected distributions have zero histogram and `null` energy moments.

`diagnostics.species` reports launched, collected, entrance-escaped and unresolved particles, their fractions, transit time/RF periods, collisions, neutral counts, and the histogram integral. It also reports an energy balance including field work, neutral energy transfer and the thermal kinetic energy of replacement ions. Unresolved particles remain visible, cause `converged=false`, and produce a warning that PDFs condition on arrivals. The default integration/event budgets are bounded; resource-limit input errors are not replaced by fabricated spectra. A seeded repeat is deterministic, but sampling error still requires increasing the particle count when statistical precision matters.

## Annular radial equivalent circuit

`app.radial_model.solve_radial(settings, operating_point=None)` solves complex RF peak phasors at annular nodes on a powered electrode. A center disk or outer edge band is held at the supplied RF voltage; an ideal grounded electrode is the return. The feed footprint remains fixed when the mesh is refined. `radial_feed_radius_m` (default 0.01 m, limited by electrode size) sets the center disk; `radial_feed_width_m` (default 0.005 m, limited by electrode size) sets the edge band. The `radial_cells` count includes the feed annulus.

Every annular node has an axial branch comprising a collisional Drude bulk resistance and inductance and two series sheath capacitors:

\[
R_{b,i}=m_e\nu d_b/(e^2n_e A_i),\quad L_{b,i}=m_e d_b/(e^2n_e A_i),\quad
C_{s,i}=\epsilon_0 A_i/s_{mean}.
\]

The sheath capacitance is `dQ/dVs` from the matrix-sheath charge relation at a **positive mean drop**. Mean drops and entrance ion density can be supplied in `operating_point`, which overrides corresponding settings. Default bulk length is the gap minus both mean sheath thicknesses; overlapping mean sheaths are rejected unless an explicit bulk length is provided, in which case the inconsistent geometry is warned. The return-sheath capacitance uses the effective local area `return_area_ratio*Ai`; the corresponding lateral spreading is an assumption.

Adjacent annuli connect through the user-specified powered-electrode sheet impedance:

\[
Z_{ij}=\frac{R_{sheet}+j\omega L_{sheet}}{2\pi}\ln(r_j/r_i).
\]

`electrode_sheet_resistance_ohm` and `electrode_sheet_inductance_h` are per-square sheet values. Links adjoining the ideal feed start at the feed's physical boundary. Away from the feed the solver enforces:

\[
V_i/Z_{axial,i}+\sum_j(V_i-V_j)/Z_{ij}=0.
\]

The outputs include radial electrode/sheath/bulk voltage amplitudes, phase, annular axial currents and bulk electron-absorbed power density. `Pbulk_i=0.5*abs(Ipeak_i)^2*Rbulk_i`; the factor 0.5 follows the peak-phasor convention. Dissipation in the electrode sheet is reported separately. The zero sheet-impedance limit is handled analytically and gives uniform voltage and power density. Nodal KCL, summed current and input-minus-bulk-and-sheet power residuals are reported.

Density is a **prescribed global uniform value**. The flat density signal is labeled accordingly. No radial density, particle transport, local electron temperature, or chemical balance is calculated. The model predicts a voltage/power distribution from the supplied linear electrical elements; it does not predict plasma-density uniformity from power alone. `absorbed_power_nonuniformity` and `electrode_voltage_nonuniformity` are area-weighted standard deviation divided by mean; separate `range_over_mean` values report `(maximum-minimum)/mean`.

Typical settings are `frequency_hz=40e6`, `rf_peak_voltage=250`, `cathode_diameter_m=.3`, `gap_m=.05`, `electron_density_m3=1e16`, `electron_temperature_ev=3`, `momentum_collision_frequency_hz=1e7`, `ion_density_m3=1e16`, `mean_cathode_sheath_voltage_v=100`, `mean_anode_sheath_voltage_v=30`, `radial_cells=32`, `radial_feed="center"`, `electrode_sheet_resistance_ohm=.02` and `electrode_sheet_inductance_h=2e-9`. These illustrative defaults can exceed the small-signal and axial penetration screens; the output warns explicitly. Sheet values and momentum frequency are input assumptions, not fitted reactor coefficients.

## Electromagnetic and model-domain screens

`electromagnetics_validity` reports the Debye length, electron plasma frequency, homogeneous Drude skin depth/bulk wavelength and a separate effective radial circuit wavelength. With `exp(j*omega*t)` and `exp(-j*k*z)`,

\[
\sigma=\frac{n_e e^2}{m_e(\nu+j\omega)},\quad k_{bulk}^2=\omega^2/c^2-j\mu_0\omega\sigma,
\quad k_{radial}^2=-Z_{sheet}/(Z_{axial} A).
\]

The skin depth is `1/abs(Im(kbulk))`, and the phase wavelength is `2*pi/abs(Re(k))`. An unbounded length is represented by `null`. The radial wavelength comes from the distributed circuit; it is not a full CCP electromagnetic surface eigenmode.

Explicit engineering screening choices warn when Debye length exceeds 0.1 bulk lengths, bulk length exceeds 0.3 skin depths, vacuum phase across the radius exceeds 0.3 radians, complex radial phase per solved cell exceeds 0.2, sheaths overlap, or RF sheath amplitude exceeds 0.3 mean sheath drops. These thresholds are disclosed cautions, **not experimentally validated domain boundaries**. Radiation, skin profiles, nonlinear sheath collapse, harmonic coupling, radial plasma conduction and electromagnetic mode coupling remain excluded even when the screens pass.

`converged` describes the electrical residuals. `diagnostics.model_domain_valid` describes these necessary screens and is not a validation claim. A numerically conserved result may therefore include model-domain warnings.

## Checked limits and tests

The focused tests check analytic DC energy/transit/Bohm flux, mass/density species fluxes, normalized PDFs, RF transit filtering and temporal refinement, seeded charge exchange/energy partition, and explicit unresolved-particle accounting. Radial tests check the analytic ideal-sheet uniform limit, both feed locations' KCL and power balances, 16/32/64-cell refinement with fixed feed geometry, Drude/Debye estimates, and large-signal warnings. Run them from `backend` with:

```sh
../.venv/bin/python -m pytest -q tests/test_ion_transport.py tests/test_radial_model.py
```

The physical elements use Bohm, matrix-sheath and Drude principles described by Lieberman and Lichtenberg, *Principles of Plasma Discharges and Materials Processing*, 2nd edition (2005), [DOI](https://doi.org/10.1002/0471724254). This implementation's slab trajectories and annular nodal network are documented approximations; no page-specific coefficient verification or experimental calibration is claimed.

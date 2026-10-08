import {useId, useState} from 'react';
import type {Json} from './types';
import {ScientificInput} from './ScientificInput';

const units = {
  ps: {label: 'ps', seconds: 1e-12},
  ns: {label: 'ns', seconds: 1e-9},
  us: {label: 'µs', seconds: 1e-6},
  ms: {label: 'ms', seconds: 1e-3},
  s: {label: 's', seconds: 1},
};
export type TimeUnit = keyof typeof units;

export function TimeInput({label, value, onChange, defaultUnit, fallback, min}: {
  label: string;
  value: Json | undefined;
  onChange: (seconds: number) => void;
  defaultUnit: TimeUnit;
  fallback?: number;
  min?: number;
}) {
  const id = useId();
  const [unit, setUnit] = useState<TimeUnit>(defaultUnit);
  const seconds = typeof value === 'number' ? value : fallback;
  return <div className="time-field">
    <label htmlFor={id}>{label}</label>
    <div className="input-unit time-input">
      <ScientificInput id={id} label={label} value={seconds} scale={units[unit].seconds}
        min={min === undefined ? undefined : min / units[unit].seconds}
        onChange={onChange}/>
      <select aria-label={`${label}の単位`} value={unit}
        onChange={e => setUnit(e.target.value as TimeUnit)}>
        {Object.entries(units).map(([key, option]) => <option key={key} value={key}>{option.label}</option>)}
      </select>
    </div>
  </div>;
}

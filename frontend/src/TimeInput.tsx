import {useEffect, useId, useRef, useState} from 'react';
import type {Json} from './types';

const units = {
  ps: {label: 'ps', seconds: 1e-12},
  ns: {label: 'ns', seconds: 1e-9},
  us: {label: 'µs', seconds: 1e-6},
  ms: {label: 'ms', seconds: 1e-3},
  s: {label: 's', seconds: 1},
};
export type TimeUnit = keyof typeof units;

function displayTime(seconds: number | undefined, unit: TimeUnit) {
  return seconds === undefined ? '' : String(Number((seconds / units[unit].seconds).toPrecision(12)));
}

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
  const [text, setText] = useState(() => displayTime(seconds, defaultUnit));
  const emitted = useRef(seconds);
  const previousUnit = useRef(unit);
  useEffect(() => {
    if (seconds !== emitted.current || unit !== previousUnit.current) {
      setText(displayTime(seconds, unit));
      emitted.current = seconds;
      previousUnit.current = unit;
    }
  }, [seconds, unit]);

  function edit(next: string) {
    setText(next);
    if (!next.trim()) return;
    const converted = Number(next) * units[unit].seconds;
    if (!Number.isFinite(converted)) return;
    emitted.current = converted;
    onChange(converted);
  }

  return <div className="time-field">
    <label htmlFor={id}>{label}</label>
    <div className="input-unit time-input">
      <input id={id} aria-label={label} type="number" step="any"
        min={min === undefined ? undefined : min / units[unit].seconds}
        value={text} onChange={e => edit(e.target.value)}
        onBlur={() => setText(displayTime(seconds, unit))}/>
      <select aria-label={`${label}の単位`} value={unit}
        onChange={e => setUnit(e.target.value as TimeUnit)}>
        {Object.entries(units).map(([key, option]) => <option key={key} value={key}>{option.label}</option>)}
      </select>
    </div>
  </div>;
}

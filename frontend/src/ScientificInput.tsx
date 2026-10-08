import {useEffect, useRef, useState} from 'react';

const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;

function display(value: number | undefined, scale: number) {
  if (value === undefined) return '';
  return scale === 1 ? String(value) : String(Number((value / scale).toPrecision(12)));
}

/** Keep the draft (including an unfinished exponent) separate from SI data. */
export function ScientificInput({value, onChange, onClear, label, id, scale = 1, min, max, minExclusive, maxExclusive,
  required = value !== undefined, placeholder, integer = false}: {
  value: number | undefined;
  onChange: (value: number) => void;
  onClear?: () => void;
  label?: string;
  id?: string;
  scale?: number;
  min?: number;
  max?: number;
  minExclusive?: boolean;
  maxExclusive?: boolean;
  required?: boolean;
  placeholder?: string;
  integer?: boolean;
}) {
  const [text, setText] = useState(() => display(value, scale));
  const emitted = useRef(value);
  const previousScale = useRef(scale);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (value !== emitted.current || scale !== previousScale.current) {
      setText(display(value, scale));
      emitted.current = value;
      previousScale.current = scale;
    }
  }, [value, scale]);

  function validation(draft: string) {
    const trimmed = draft.trim();
    if (!trimmed) return required ? '数値を入力してください。' : '';
    const numeric = Number(trimmed);
    const converted = numeric * scale;
    if (!decimal.test(trimmed) || !Number.isFinite(numeric) || !Number.isFinite(converted))
      return '有限の数値を入力してください（例: 4e7、1e-9）。';
    if ((numeric === 0 && /[1-9]/.test(trimmed.split(/[eE]/)[0])) || (numeric !== 0 && converted === 0))
      return '数値が小さすぎます。';
    if (integer && !Number.isInteger(converted)) return '整数を入力してください。';
    if (min !== undefined && (minExclusive ? numeric <= min : numeric < min))
      return minExclusive ? `${min}より大きい数値を入力してください。` : `${min}以上の数値を入力してください。`;
    if (max !== undefined && (maxExclusive ? numeric >= max : numeric > max))
      return maxExclusive ? `${max}より小さい数値を入力してください。` : `${max}以下の数値を入力してください。`;
    return '';
  }
  const error = validation(text);
  useEffect(() => {input.current?.setCustomValidity(error);}, [error]);

  function edit(next: string) {
    setText(next);
    input.current?.setCustomValidity(validation(next));
    if (!next.trim()) {
      if (onClear) {emitted.current = undefined; onClear();}
      return;
    }
    if (validation(next)) return;
    const converted = Number(next) * scale;
    emitted.current = converted;
    onChange(converted);
  }
  return <input ref={input} id={id} aria-label={label} type="text" inputMode="text"
    data-scientific-input spellCheck={false} autoComplete="off" required={required}
    aria-invalid={error ? true : undefined} placeholder={placeholder}
    value={text} onChange={e => edit(e.target.value)}/>;
}

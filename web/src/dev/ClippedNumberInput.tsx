import { useEffect, useState } from "react";

type Props = {
  id?: string;
  value: string; min: number; max: number; disabled: boolean;
  onChange: (value: string) => string | void; integer?: boolean; decimals?: number;
  type?: "number" | "text";
};

export default function ClippedNumberInput({ id, value, min, max, disabled, onChange, integer = false, decimals, type = "number" }: Props) {
  const [draft, setDraft] = useState(value);
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    setDraft(previous => previous.trim() !== "" && Number(previous) === Number(value) ? previous : value);
  }, [value]);
  return <input id={id} type={type} inputMode="decimal" min={min} max={max} step={integer ? "1" : "any"} disabled={disabled}
    value={focused ? draft : decimals === undefined ? value : Number(value).toFixed(decimals)}
    onFocus={() => { setFocused(true); setDraft(value); }}
    onBlur={() => { setFocused(false); setDraft(value); }}
    onChange={event => {
      const raw = event.target.value;
      if (raw === "" || !Number.isFinite(Number(raw))) { setDraft(raw); return; }
      const bounded = Math.max(min, Math.min(max, Number(raw)));
      const clipped = String(integer ? Math.trunc(bounded) : bounded);
      const accepted = onChange(clipped) ?? clipped;
      setDraft(!integer && Number(raw) === Number(accepted) ? raw : accepted);
    }} />;
}

import type { ReactNode } from "react";

export default function ParameterHelp({ name, children }: { name: string; children: ReactNode }) {
  return <details className="parameter-help">
    <summary aria-label={`${name}の説明`} title={`${name}の説明を開く`}>ⓘ</summary>
    <div className="parameter-help-text">{children}</div>
  </details>;
}

import type { GenerationFailure, GenerationResult, ValidationCheck } from "../api/client";

// So exibe o que o backend decidiu (Validation Engine). Nenhuma regra aqui.

const CHECK_LABELS: Record<string, string> = {
  face_identity: "Rosto (vs master)",
  subject_count: "Uma só Luna",
  anatomy: "Anatomia",
  pose: "Pose",
  body_consistency: "Corpo (vs master)",
  age: "Idade aparente",
  trigger_leak: "Gatilho escrito",
};

const STATUS_TEXT: Record<string, string> = {
  PASS: "ok",
  FAIL: "falhou",
  UNKNOWN: "não verificado",
  NOT_COMPARABLE: "não comparável",
  INFORMATIONAL: "informativo",
};

const RESULT_TEXT: Record<string, string> = {
  PASS: "✓ Aprovada",
  PASS_WITH_UNKNOWN: "✓ Aprovada (com itens não verificados)",
  FAIL: "✕ Reprovada",
};

function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

interface Props {
  result: GenerationResult;
  failures?: GenerationFailure[];
  compact?: boolean;
}

export function ValidationReportView({ result, failures = [], compact = false }: Props) {
  const report = result.validation;
  const mine = failures.filter((f) => f.result_id === result.id);
  const status = report?.status ?? "FAIL";
  const m = result.metrics;

  return (
    <div className={`identity-score ${report && status !== "FAIL" ? "is-accepted" : "is-rejected"}`}>
      <div className="identity-score-grid">
        <div>
          <span className="identity-score-label">Rosto</span>
          <strong className="identity-score-big">{fmt(result.face_score)}</strong>
        </div>
        <div>
          <span className="identity-score-label">Resultado</span>
          <strong className="identity-score-status">{report ? RESULT_TEXT[status] : "✕ Erro"}</strong>
        </div>
        {m && (
          <div>
            <span className="identity-score-label">Tempo · custo</span>
            <strong>
              {Math.round(m.total_seconds)} s ·{" "}
              {m.estimated_cost_usd === null ? "custo não medido" : `US$ ${m.estimated_cost_usd.toFixed(4)}`}
            </strong>
          </div>
        )}
      </div>
      {result.error && <p className="error small">{result.error}</p>}

      {mine.length > 0 && (
        <ul className="identity-score-reasons">
          {mine.map((f) => (
            <li key={f.id}>
              <strong>{f.failure_type}</strong>
              {f.reason && <span className="muted small"> · {f.reason}</span>}
            </li>
          ))}
        </ul>
      )}

      {!compact && report && (
        <dl className="identity-metrics">
          {Object.values(report.checks).map((c: ValidationCheck) => (
            <div key={c.name} className={c.status === "UNKNOWN" || c.status === "NOT_COMPARABLE" ? "is-unmeasured" : ""}>
              <dt>
                {CHECK_LABELS[c.name] ?? c.name}{" "}
                <span className={`check-status check-${c.status.toLowerCase()}`}>{STATUS_TEXT[c.status] ?? c.status}</span>
              </dt>
              <dd className="muted small">
                {c.score !== null && `${fmt(c.score)}${c.threshold !== null ? ` / limiar ${fmt(c.threshold)}` : ""} · `}
                {c.reason} <span className="muted">(confiança {c.confidence})</span>
              </dd>
            </div>
          ))}
        </dl>
      )}
      {!compact && m && (
        <p className="muted small">
          {m.execution_mode === "BATCH_MODE" ? "Em lote" : "Pedido avulso"} · tentativa {result.attempt} (
          {result.strategy}) · semente {result.seeds.scene}/{result.seeds.face_lock}
          {m.gpu ? ` · ${m.gpu}` : ""}
          {m.model_switches ? ` · ${m.model_switches} troca(s) de modelo` : ""}
        </p>
      )}
    </div>
  );
}

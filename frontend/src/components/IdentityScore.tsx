import type { GenerationFailure, GenerationResult } from "../api/client";

const METRIC_LABELS: Record<string, string> = {
  face_similarity: "Rosto",
  facial_structure: "Estrutura do rosto",
  hair: "Cabelo",
  body: "Corpo",
  age: "Idade",
  distinctive_features: "Traços marcantes",
};

const FAILURE_LABELS: Record<string, string> = {
  facial_structure_mismatch: "Rosto diferente da persona",
  eye_mismatch: "Olhos diferentes",
  nose_mismatch: "Nariz diferente",
  mouth_mismatch: "Boca diferente",
  hairstyle_mismatch: "Cabelo diferente",
  age_mismatch: "Idade diferente",
  body_mismatch: "Corpo diferente",
  distinctive_feature_missing: "Faltou um traço marcante",
  low_identity_confidence: "Identidade incerta",
  face_not_found: "Nenhum rosto na imagem",
  sex_mismatch: "Rosto do outro sexo",
  provider_error: "O modelo falhou",
};

function pct(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
}

interface Props {
  result: GenerationResult;
  failures?: GenerationFailure[];
  threshold?: number;
  compact?: boolean;
}

// Resultado da validacao de identidade de uma tentativa: nota, rosto,
// aparencia, status e o motivo da reprovacao.
export function IdentityScore({ result, failures = [], threshold, compact = false }: Props) {
  const accepted = result.status === "ACCEPT";
  const mine = failures.filter((f) => f.result_id === result.id);
  const metrics = result.validation?.metrics ?? {};

  return (
    <div className={`identity-score ${accepted ? "is-accepted" : "is-rejected"}`}>
      <div className="identity-score-grid">
        <div>
          <span className="identity-score-label">Identidade</span>
          <strong className="identity-score-big">{pct(result.identity_score, 1)}</strong>
        </div>
        <div>
          <span className="identity-score-label">Rosto</span>
          <strong>{pct(result.face_score)}</strong>
        </div>
        <div>
          <span className="identity-score-label">Aparência</span>
          <strong>{pct(result.appearance_score)}</strong>
        </div>
        <div>
          <span className="identity-score-label">Status</span>
          <strong className="identity-score-status">
            {result.status === "ERROR" ? "✕ Erro" : accepted ? "✓ Aceita" : "✕ Rejeitada"}
          </strong>
        </div>
      </div>
      {threshold !== undefined && <p className="muted small">Limiar: {pct(threshold)}</p>}

      {mine.length > 0 && (
        <ul className="identity-score-reasons">
          {mine.map((f) => (
            <li key={f.id}>
              <strong>{FAILURE_LABELS[f.failure_type] ?? f.failure_type}</strong>
              <span className="muted small"> · {f.details}</span>
            </li>
          ))}
        </ul>
      )}

      {!compact && Object.keys(metrics).length > 0 && (
        <dl className="identity-metrics">
          {Object.values(metrics).map((m) => (
            <div key={m.name} className={m.measured ? "" : "is-unmeasured"}>
              <dt>
                {METRIC_LABELS[m.name] ?? m.name} <span className="muted small">peso {pct(m.weight)}</span>
              </dt>
              <dd>
                {m.measured ? (
                  <>
                    <span className="identity-bar">
                      <span style={{ width: pct(m.score) }} />
                    </span>
                    {pct(m.score)}
                  </>
                ) : (
                  <span className="muted small">não medido · {m.note}</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {!compact && result.validation && (
        <p className="muted small">Parte da nota medida de fato: {pct(result.validation.coverage)}</p>
      )}
    </div>
  );
}

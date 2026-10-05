import { useEffect, useRef, useState } from "react";
import type { EngineReference, PersonaReference, ReferenceType } from "../api/client";
import {
  REFERENCE_TYPES,
  deletePersonaReference,
  getEnginePersona,
  personaReferenceFileUrl,
  setPrimaryPersonaReference,
  updateEngineReference,
  uploadPersonaReference,
} from "../api/client";

const TYPE_LABELS: Record<ReferenceType, string> = {
  PRIMARY: "Principal",
  FACE: "Rosto",
  FULL_BODY: "Corpo inteiro",
  PROFILE: "Perfil",
  STYLE: "Estilo",
  OTHER: "Outra",
};

interface Props {
  personaId: string;
  references: PersonaReference[];
  onChange: (references: PersonaReference[]) => void;
}

export function PersonaReferences({ personaId, references, onChange }: Props) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Tipo, peso e ativa (Persona Engine). Rosto/Perfil ativos sao a base da
  // validacao de identidade.
  const [engine, setEngine] = useState<Record<string, EngineReference>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getEnginePersona(personaId)
      .then((p) => setEngine(Object.fromEntries((p.references ?? []).map((r) => [r.id, r]))))
      .catch(() => setEngine({}));
  }, [personaId, references]);

  async function handleEngineChange(referenceId: string, patch: Partial<Pick<EngineReference, "type" | "weight" | "active">>) {
    setError(null);
    try {
      const updated = await updateEngineReference(personaId, referenceId, patch);
      if (patch.type === "PRIMARY") {
        onChange(references.map((r) => ({ ...r, is_primary: r.id === referenceId })));
      } else {
        setEngine((prev) => ({ ...prev, [referenceId]: updated }));
      }
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  async function handleFilesSelected(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      const uploaded: PersonaReference[] = [];
      for (const file of Array.from(files)) {
        uploaded.push(await uploadPersonaReference(personaId, file));
      }
      onChange([...references, ...uploaded]);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleDelete(referenceId: string) {
    setError(null);
    try {
      await deletePersonaReference(personaId, referenceId);
      onChange(references.filter((r) => r.id !== referenceId));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  // A principal e a foto que aparece no card da persona (tela Gerar).
  async function handleSetPrimary(referenceId: string) {
    setError(null);
    try {
      onChange(await setPrimaryPersonaReference(personaId, referenceId));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  return (
    <div>
      <h3>Referências ({references.length})</h3>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple
        onChange={(e) => handleFilesSelected(e.target.files)}
        disabled={uploading}
      />
      {uploading && <p className="muted small">Enviando...</p>}
      {error && <p className="error small">{error}</p>}

      {references.length === 0 ? (
        <p className="muted">Nenhuma referência importada ainda.</p>
      ) : (
        <div className="reference-grid">
          {references.map((ref) => (
            <div key={ref.id} className="reference-card">
              <img src={personaReferenceFileUrl(personaId, ref.id)} alt={ref.original_filename} />
              {ref.is_primary && <span className="reference-badge">principal</span>}
              <p className="muted small reference-name">{ref.original_filename}</p>
              {engine[ref.id] && (
                <div className="reference-engine">
                  <select
                    aria-label="Tipo da foto"
                    value={engine[ref.id].type}
                    onChange={(e) => handleEngineChange(ref.id, { type: e.target.value as ReferenceType })}
                  >
                    {REFERENCE_TYPES.map((t) => (
                      <option key={t} value={t} disabled={t === "PRIMARY" && !engine[ref.id].active}>
                        {TYPE_LABELS[t]}
                      </option>
                    ))}
                  </select>
                  <label className="reference-weight">
                    Peso {Math.round(engine[ref.id].weight * 100)}%
                    <input
                      type="range"
                      min={0}
                      max={100}
                      defaultValue={Math.round(engine[ref.id].weight * 100)}
                      onMouseUp={(e) => handleEngineChange(ref.id, { weight: Number(e.currentTarget.value) / 100 })}
                      onTouchEnd={(e) => handleEngineChange(ref.id, { weight: Number(e.currentTarget.value) / 100 })}
                      onKeyUp={(e) => handleEngineChange(ref.id, { weight: Number(e.currentTarget.value) / 100 })}
                    />
                  </label>
                  {!ref.is_primary && (
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={engine[ref.id].active}
                        onChange={(e) => handleEngineChange(ref.id, { active: e.target.checked })}
                      />
                      Usar na validação
                    </label>
                  )}
                </div>
              )}
              {!ref.is_primary && (
                <button type="button" className="small" onClick={() => handleSetPrimary(ref.id)}>
                  Definir como principal
                </button>
              )}
              <button type="button" className="danger small" onClick={() => handleDelete(ref.id)}>
                Excluir
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

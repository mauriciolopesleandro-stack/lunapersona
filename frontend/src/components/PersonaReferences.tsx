import { useRef, useState } from "react";
import type { PersonaReference } from "../api/client";
import {
  deletePersonaReference,
  personaReferenceFileUrl,
  setPrimaryPersonaReference,
  uploadPersonaReference,
} from "../api/client";

interface Props {
  personaId: string;
  references: PersonaReference[];
  onChange: (references: PersonaReference[]) => void;
}

export function PersonaReferences({ personaId, references, onChange }: Props) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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

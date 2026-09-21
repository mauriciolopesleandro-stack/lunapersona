import { useEffect, useState } from "react";
import { FIXED_IDENTITY_FIELDS, VARIABLE_DEFAULT_FIELDS, updatePersonaIdentity } from "../api/client";
import type { PersonaDetail } from "../api/client";

const FIXED_LABELS: Record<string, string> = {
  formato_rosto: "Formato do rosto",
  caracteristicas_faciais: "Características faciais",
  olhos: "Olhos",
  sobrancelhas: "Sobrancelhas",
  nariz: "Nariz",
  boca: "Boca",
  formato_cabelo: "Formato do cabelo",
  cor_cabelo: "Cor do cabelo",
  textura_cabelo: "Textura do cabelo",
  tom_pele: "Tom de pele",
  caracteristicas_corporais: "Características corporais",
  caracteristicas_visuais_permanentes: "Outras características permanentes",
};

const VARIABLE_LABELS: Record<string, string> = {
  roupa: "Roupa",
  cenario: "Cenário",
  iluminacao: "Iluminação",
  pose: "Pose",
  expressao: "Expressão",
  camera: "Câmera",
};

interface Props {
  persona: PersonaDetail;
  onSaved: (persona: PersonaDetail) => void;
}

export function PersonaIdentityForm({ persona, onSaved }: Props) {
  const [fixed, setFixed] = useState<Record<string, string>>({});
  const [variableDefaults, setVariableDefaults] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    setFixed({ ...persona.identity.fixed });
    setVariableDefaults({ ...persona.identity.variable_defaults });
  }, [persona]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const updated = await updatePersonaIdentity(persona.id, { fixed, variable_defaults: variableDefaults });
      onSaved(updated);
      setSavedAt(Date.now());
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h3>Características fixas (identidade)</h3>
      <p className="muted small">
        Não devem mudar entre gerações — descrevem quem a Luna é, não o que ela está vestindo ou onde está.
      </p>
      <div className="identity-grid">
        {FIXED_IDENTITY_FIELDS.map((field) => (
          <div key={field}>
            <label htmlFor={`fixed-${field}`}>{FIXED_LABELS[field]}</label>
            <input
              id={`fixed-${field}`}
              type="text"
              value={fixed[field] ?? ""}
              onChange={(e) => setFixed((prev) => ({ ...prev, [field]: e.target.value }))}
            />
          </div>
        ))}
      </div>

      <h3>Padrões variáveis (opcional)</h3>
      <p className="muted small">Valores sugeridos para roupa/cenário/etc. — podem ser sobrescritos a cada geração.</p>
      <div className="identity-grid">
        {VARIABLE_DEFAULT_FIELDS.map((field) => (
          <div key={field}>
            <label htmlFor={`var-${field}`}>{VARIABLE_LABELS[field]}</label>
            <input
              id={`var-${field}`}
              type="text"
              value={variableDefaults[field] ?? ""}
              onChange={(e) => setVariableDefaults((prev) => ({ ...prev, [field]: e.target.value }))}
            />
          </div>
        ))}
      </div>

      {error && <p className="error small">{error}</p>}
      <button type="button" onClick={handleSave} disabled={saving}>
        {saving ? "Salvando..." : "Salvar Identity Profile"}
      </button>
      {savedAt && !saving && <span className="muted small saved-hint"> Salvo.</span>}
    </div>
  );
}

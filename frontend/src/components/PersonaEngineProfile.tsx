import { useEffect, useState } from "react";
import type { EnginePersona } from "../api/client";
import { APPEARANCE_FIELDS, STYLE_FIELDS, getEnginePersona, updateEnginePersona } from "../api/client";

const LABELS: Record<string, string> = {
  roupa: "Roupa padrão",
  expressao: "Expressão padrão",
  maquiagem: "Maquiagem",
  acessorios: "Acessórios",
  fotografia: "Tipo de fotografia",
  iluminacao: "Iluminação",
  composicao: "Composição",
  estetica: "Estética",
  tratamento: "Tratamento da imagem",
  realismo: "Nível de realismo",
  camera: "Câmera",
};

const lines = (text: string) =>
  text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);

interface Props {
  personaId: string;
}

// Perfil da persona no Persona Engine: o que nao muda (idade, sexo, tracos
// marcantes) separado da aparencia configuravel, do estilo da foto e das
// restricoes. Os tracos do rosto ficam na aba Identidade.
export function PersonaEngineProfile({ personaId }: Props) {
  const [persona, setPersona] = useState<EnginePersona | null>(null);
  const [age, setAge] = useState("");
  const [sex, setSex] = useState<"" | "F" | "M">("");
  const [keywords, setKeywords] = useState("");
  const [appearance, setAppearance] = useState<Record<string, string>>({});
  const [style, setStyle] = useState<Record<string, string>>({});
  const [rules, setRules] = useState("");
  const [negative, setNegative] = useState("");
  const [threshold, setThreshold] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function fill(p: EnginePersona) {
    setPersona(p);
    setAge(p.identity.apparent_age ? String(p.identity.apparent_age) : "");
    setSex(p.identity.sex);
    setKeywords(p.identity.distinctive_keywords.join(", "));
    setAppearance(p.appearance.values);
    setStyle(p.style.values);
    setRules(p.constraints.rules.join("\n"));
    setNegative(p.constraints.negative.join("\n"));
    setThreshold(p.validation.threshold === null ? "" : String(Math.round(p.validation.threshold * 100)));
  }

  useEffect(() => {
    setPersona(null);
    getEnginePersona(personaId)
      .then(fill)
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }, [personaId]);

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const updated = await updateEnginePersona(personaId, {
        identity: {
          apparent_age: age ? Number(age) : null,
          sex,
          distinctive_keywords: keywords
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        },
        appearance: Object.fromEntries(APPEARANCE_FIELDS.map((k) => [k, appearance[k] ?? ""])),
        style: Object.fromEntries(STYLE_FIELDS.map((k) => [k, style[k] ?? ""])),
        constraints: { rules: lines(rules), negative: lines(negative) },
        validation: { threshold: threshold ? Number(threshold) / 100 : null },
      });
      fill(updated);
      setSaved(true);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setSaving(false);
    }
  }

  if (!persona) {
    return error ? <p className="error small">{error}</p> : <p className="muted">Carregando...</p>;
  }

  return (
    <div>
      <p className="muted small">
        Versão {persona.version} · atualizada em {new Date(persona.updated_at).toLocaleString("pt-BR")}
      </p>

      <h3>Identidade (o que não muda)</h3>
      <p className="muted small">Os traços do rosto e do corpo ficam na aba Identidade. Aqui entra o que a validação confere.</p>
      <div className="identity-grid">
        <div>
          <label htmlFor="eng-age">Idade aparente</label>
          <input id="eng-age" type="number" min={1} max={120} value={age} onChange={(e) => setAge(e.target.value)} />
        </div>
        <div>
          <label htmlFor="eng-sex">Sexo (a validação recusa rosto do outro sexo)</label>
          <select id="eng-sex" value={sex} onChange={(e) => setSex(e.target.value as "" | "F" | "M")}>
            <option value="">Não conferir</option>
            <option value="F">Feminino</option>
            <option value="M">Masculino</option>
          </select>
        </div>
        <div>
          <label htmlFor="eng-keywords">Traços marcantes (em inglês, separados por vírgula)</label>
          <input
            id="eng-keywords"
            value={keywords}
            placeholder="necklace, choker"
            onChange={(e) => setKeywords(e.target.value)}
          />
        </div>
      </div>

      <h3>Aparência</h3>
      <div className="identity-grid">
        {APPEARANCE_FIELDS.map((k) => (
          <div key={k}>
            <label htmlFor={`eng-app-${k}`}>{LABELS[k]}</label>
            <input
              id={`eng-app-${k}`}
              value={appearance[k] ?? ""}
              onChange={(e) => setAppearance({ ...appearance, [k]: e.target.value })}
            />
          </div>
        ))}
      </div>

      <h3>Estilo</h3>
      <div className="identity-grid">
        {STYLE_FIELDS.map((k) => (
          <div key={k}>
            <label htmlFor={`eng-style-${k}`}>{LABELS[k]}</label>
            <input
              id={`eng-style-${k}`}
              value={style[k] ?? ""}
              onChange={(e) => setStyle({ ...style, [k]: e.target.value })}
            />
          </div>
        ))}
      </div>

      <h3>Restrições</h3>
      <div className="identity-grid">
        <div>
          <label htmlFor="eng-rules">Regras (uma por linha)</label>
          <textarea id="eng-rules" rows={3} value={rules} onChange={(e) => setRules(e.target.value)} />
        </div>
        <div>
          <label htmlFor="eng-negative">Evitar (uma por linha)</label>
          <textarea id="eng-negative" rows={3} value={negative} onChange={(e) => setNegative(e.target.value)} />
        </div>
      </div>

      <h3>Validação de identidade</h3>
      <div className="identity-grid">
        <div>
          <label htmlFor="eng-threshold">Limiar desta persona (%; vazio = padrão do sistema)</label>
          <input
            id="eng-threshold"
            type="number"
            min={0}
            max={100}
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
          />
        </div>
      </div>

      {error && <p className="error small">{error}</p>}
      {saved && <p className="saved-hint">Salvo (versão {persona.version}).</p>}
      <button type="button" className="primary" onClick={handleSave} disabled={saving}>
        {saving ? "Salvando..." : "Salvar perfil"}
      </button>
    </div>
  );
}

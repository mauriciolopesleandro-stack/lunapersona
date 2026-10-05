import { useEffect, useState } from "react";
import type { ModelInfo, PersonaDetail, PersonaSummary, WorkflowInfo } from "../api/client";
import { createEnginePersona, deactivateEnginePersona, getPersona, getPersonas } from "../api/client";
import { PersonaEngineGenerate } from "./PersonaEngineGenerate";
import { PersonaEngineProfile } from "./PersonaEngineProfile";
import { PersonaGenerationHistory } from "./PersonaGenerationHistory";
import { PersonaIdentityForm } from "./PersonaIdentityForm";
import { PersonaReferences } from "./PersonaReferences";
import { PersonaSettings } from "./PersonaSettings";

type SubTab = "identidade" | "referencias" | "perfil" | "gerar" | "historico" | "configuracoes";

const SUB_TABS: { id: SubTab; label: string }[] = [
  { id: "identidade", label: "Identidade" },
  { id: "referencias", label: "Referências" },
  { id: "perfil", label: "Aparência, estilo e regras" },
  { id: "gerar", label: "Gerar com validação" },
  { id: "historico", label: "Histórico" },
  { id: "configuracoes", label: "Configurações" },
];

interface Props {
  models: ModelInfo[];
  workflows: WorkflowInfo[];
  // Fotos mudaram (envio, exclusao, nova principal): a tela Gerar recarrega
  // a miniatura do card da persona.
  onReferencesChanged?: () => void;
  // Lista de personas mudou (criada ou desativada): o resto do site recarrega.
  onPersonasChanged?: () => void;
  ensureAwake?: () => Promise<unknown>;
}

export function PersonasView({ models, workflows, onReferencesChanged, onPersonasChanged, ensureAwake }: Props) {
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [persona, setPersona] = useState<PersonaDetail | null>(null);
  const [subTab, setSubTab] = useState<SubTab>("identidade");
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  function loadList(select?: string) {
    return getPersonas()
      .then((list) => {
        setPersonas(list);
        setSelectedId(select ?? (list.length > 0 ? list[0].id : null));
      })
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }

  useEffect(() => {
    loadList();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setPersona(null);
      return;
    }
    getPersona(selectedId)
      .then(setPersona)
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }, [selectedId]);

  async function handleCreate() {
    if (!newName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const created = await createEnginePersona({ name: newName.trim() });
      setNewName("");
      await loadList(created.id);
      setSubTab("referencias");
      onPersonasChanged?.();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setCreating(false);
    }
  }

  async function handleDeactivate() {
    if (!persona) return;
    if (!window.confirm(`Desativar ${persona.name}? Ela some das listas, mas fotos, LoRA, voz e histórico ficam guardados.`)) {
      return;
    }
    setError(null);
    try {
      await deactivateEnginePersona(persona.id);
      await loadList();
      onPersonasChanged?.();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  return (
    <div className="personas-view">
      <aside className="persona-list">
        <h2>Personas</h2>
        {personas.length === 0 && <p className="muted small">Nenhuma persona encontrada.</p>}
        <ul>
          {personas.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className={p.id === selectedId ? "persona-item active" : "persona-item"}
                onClick={() => setSelectedId(p.id)}
              >
                {p.name}
                <span className="muted small"> · {p.reference_count} ref.</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="persona-create">
          <label htmlFor="new-persona-name">Nova persona</label>
          <input
            id="new-persona-name"
            value={newName}
            placeholder="Nome"
            maxLength={80}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          />
          <button type="button" className="small" onClick={handleCreate} disabled={creating || !newName.trim()}>
            {creating ? "Criando..." : "Criar"}
          </button>
        </div>
      </aside>

      <section className="persona-detail">
        {error && <p className="error small">{error}</p>}
        {!persona ? (
          <p className="muted">Selecione uma persona.</p>
        ) : (
          <>
            <h2>{persona.name}</h2>
            {persona.description && <p className="muted small">{persona.description}</p>}

            <nav className="sub-tabs">
              {SUB_TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={subTab === t.id ? "active" : ""}
                  onClick={() => setSubTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </nav>

            {subTab === "identidade" && <PersonaIdentityForm persona={persona} onSaved={setPersona} />}
            {subTab === "referencias" && (
              <PersonaReferences
                personaId={persona.id}
                references={persona.references}
                onChange={(references) => {
                  setPersona({ ...persona, references });
                  onReferencesChanged?.();
                }}
              />
            )}
            {subTab === "perfil" && <PersonaEngineProfile personaId={persona.id} />}
            {subTab === "gerar" && (
              <PersonaEngineGenerate personaId={persona.id} personaName={persona.name} ensureAwake={ensureAwake} />
            )}
            {subTab === "historico" && <PersonaGenerationHistory personaId={persona.id} ensureAwake={ensureAwake} />}
            {subTab === "configuracoes" && (
              <>
                <PersonaSettings persona={persona} models={models} workflows={workflows} onSaved={setPersona} />
                <h3>Desativar persona</h3>
                <p className="muted small">Some das listas; nada é apagado do volume.</p>
                <button type="button" className="danger small" onClick={handleDeactivate}>
                  Desativar {persona.name}
                </button>
              </>
            )}
          </>
        )}
      </section>
    </div>
  );
}

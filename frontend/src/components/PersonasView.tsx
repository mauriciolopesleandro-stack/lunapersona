import { useEffect, useState } from "react";
import type { ModelInfo, PersonaDetail, PersonaSummary, WorkflowInfo } from "../api/client";
import { getPersona, getPersonas } from "../api/client";
import { PersonaIdentityForm } from "./PersonaIdentityForm";
import { PersonaReferences } from "./PersonaReferences";
import { PersonaSettings } from "./PersonaSettings";

type SubTab = "identidade" | "referencias" | "configuracoes";

interface Props {
  models: ModelInfo[];
  workflows: WorkflowInfo[];
}

export function PersonasView({ models, workflows }: Props) {
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [persona, setPersona] = useState<PersonaDetail | null>(null);
  const [subTab, setSubTab] = useState<SubTab>("identidade");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPersonas()
      .then((list) => {
        setPersonas(list);
        if (list.length > 0) setSelectedId(list[0].id);
      })
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    getPersona(selectedId)
      .then(setPersona)
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }, [selectedId]);

  if (error) {
    return <p className="error">{error}</p>;
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
      </aside>

      <section className="persona-detail">
        {!persona ? (
          <p className="muted">Selecione uma persona.</p>
        ) : (
          <>
            <h2>{persona.name}</h2>
            {persona.description && <p className="muted small">{persona.description}</p>}

            <nav className="sub-tabs">
              <button
                type="button"
                className={subTab === "identidade" ? "active" : ""}
                onClick={() => setSubTab("identidade")}
              >
                Identidade
              </button>
              <button
                type="button"
                className={subTab === "referencias" ? "active" : ""}
                onClick={() => setSubTab("referencias")}
              >
                Referências
              </button>
              <button
                type="button"
                className={subTab === "configuracoes" ? "active" : ""}
                onClick={() => setSubTab("configuracoes")}
              >
                Configurações
              </button>
            </nav>

            {subTab === "identidade" && <PersonaIdentityForm persona={persona} onSaved={setPersona} />}
            {subTab === "referencias" && (
              <PersonaReferences
                personaId={persona.id}
                references={persona.references}
                onChange={(references) => setPersona({ ...persona, references })}
              />
            )}
            {subTab === "configuracoes" && (
              <PersonaSettings persona={persona} models={models} workflows={workflows} onSaved={setPersona} />
            )}
          </>
        )}
      </section>
    </div>
  );
}

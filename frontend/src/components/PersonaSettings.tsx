import { useState } from "react";
import type { ModelInfo, PersonaDetail, WorkflowInfo } from "../api/client";
import { updatePersonaGeneration } from "../api/client";

interface Props {
  persona: PersonaDetail;
  models: ModelInfo[];
  workflows: WorkflowInfo[];
  onSaved: (persona: PersonaDetail) => void;
}

export function PersonaSettings({ persona, models, workflows, onSaved }: Props) {
  const [modelId, setModelId] = useState(persona.generation.model_id);
  const [workflowId, setWorkflowId] = useState(persona.generation.workflow_id);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const updated = await updatePersonaGeneration(persona.id, { model_id: modelId, workflow_id: workflowId });
      onSaved(updated);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h3>Modelo e workflow padrão</h3>
      <p className="muted small">
        Usados quando esta persona é selecionada na tela de Geração (podem ser trocados por geração).
      </p>
      <div className="row">
        <div>
          <label htmlFor="persona-model">Modelo</label>
          <select id="persona-model" value={modelId} onChange={(e) => setModelId(e.target.value)}>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="persona-workflow">Workflow</label>
          <select id="persona-workflow" value={workflowId} onChange={(e) => setWorkflowId(e.target.value)}>
            {workflows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.title}
              </option>
            ))}
          </select>
        </div>
      </div>
      {error && <p className="error small">{error}</p>}
      <button type="button" onClick={handleSave} disabled={saving}>
        {saving ? "Salvando..." : "Salvar configurações"}
      </button>

      <h3>Métodos de identidade</h3>
      <p className="muted small">
        Ativos: {persona.identity_methods.active.join(", ") || "nenhum"} · Planejados (não implementados ainda):{" "}
        {persona.identity_methods.planned.filter((m) => !persona.identity_methods.active.includes(m)).join(", ")}
      </p>
    </div>
  );
}

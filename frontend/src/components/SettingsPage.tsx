import { useEffect, useState } from "react";
import type { HealthResponse, ModelInfo, PodStatus, WorkflowInfo } from "../api/client";
import { getPodStatus } from "../api/client";
import { DEFAULT_SETTINGS, getSettings, saveSettings, type GenerationSettings } from "../lib/settings";

interface Props {
  models: ModelInfo[];
  workflows: WorkflowInfo[];
  health: HealthResponse | null;
}

export function SettingsPage({ models, workflows, health }: Props) {
  const [settings, setSettings] = useState<GenerationSettings>(DEFAULT_SETTINGS);
  const [saved, setSaved] = useState(false);
  const [podStatus, setPodStatus] = useState<PodStatus | null>(null);

  useEffect(() => {
    setSettings(getSettings());
    getPodStatus()
      .then(setPodStatus)
      .catch(() => setPodStatus(null));
  }, []);

  function handleSave() {
    saveSettings(settings);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>Configurações</h1>
        <p>Padrões avançados de geração e status da infraestrutura.</p>
      </div>

      <div className="panel settings-section">
        <h2>Padrões de geração</h2>
        <p className="muted small">
          Usados em toda geração feita pela tela "Gerar" — modelo e workflow ficam escondidos por lá para manter o
          fluxo simples.
        </p>

        <div className="settings-grid">
          <div>
            <label htmlFor="s-model">Modelo padrão</label>
            <select
              id="s-model"
              value={settings.modelId || models[0]?.id || ""}
              onChange={(e) => setSettings({ ...settings, modelId: e.target.value })}
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="s-workflow">Workflow padrão</label>
            <select
              id="s-workflow"
              value={settings.workflowId || workflows[0]?.id || ""}
              onChange={(e) => setSettings({ ...settings, workflowId: e.target.value })}
            >
              {workflows.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.title}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="s-steps">Steps</label>
            <input
              id="s-steps"
              type="number"
              value={settings.steps}
              onChange={(e) => setSettings({ ...settings, steps: Number(e.target.value) })}
            />
          </div>
          <div>
            <label htmlFor="s-guidance">Guidance</label>
            <input
              id="s-guidance"
              type="number"
              step="0.1"
              value={settings.guidance}
              onChange={(e) => setSettings({ ...settings, guidance: Number(e.target.value) })}
            />
          </div>
        </div>

        <button type="button" className="primary" onClick={handleSave} style={{ marginTop: 16 }}>
          Salvar
        </button>
        {saved && <span className="muted small saved-hint">Salvo.</span>}
      </div>

      <div className="panel settings-section">
        <h2>Infraestrutura</h2>
        <dl className="kv-list">
          <dt>Backend</dt>
          <dd>{health?.backend ?? "verificando..."}</dd>
          <dt>ComfyUI</dt>
          <dd>{health ? (health.comfyui.ok ? "conectado" : "indisponível") : "verificando..."}</dd>
          <dt>Pod</dt>
          <dd>{podStatus ? (podStatus.running ? "ligado" : "desligado") : "verificando..."}</dd>
          <dt>Saldo RunPod</dt>
          <dd>{podStatus ? `$${podStatus.balance.toFixed(2)}` : "..."}</dd>
          <dt>Custo por hora</dt>
          <dd>{podStatus ? `$${podStatus.costPerHr.toFixed(2)}/h` : "..."}</dd>
        </dl>
      </div>
    </div>
  );
}

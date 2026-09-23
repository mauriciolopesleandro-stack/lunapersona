import { useState } from "react";
import type { ModelInfo, PersonaSummary, WorkflowInfo } from "../api/client";
import type { GenerationSettings } from "../lib/settings";
import { ChatAssistant, type ChatSession } from "./ChatAssistant";

interface FormatPreset {
  id: string;
  label: string;
  width: number;
  height: number;
}

const FORMATS: FormatPreset[] = [
  { id: "1:1", label: "1:1", width: 1024, height: 1024 },
  { id: "4:5", label: "4:5", width: 896, height: 1120 },
  { id: "3:4", label: "3:4", width: 896, height: 1152 },
  { id: "16:9", label: "16:9", width: 1280, height: 720 },
  { id: "9:16", label: "9:16", width: 720, height: 1280 },
];

interface StyleOption {
  id: string;
  label: string;
  suffix: string;
}

const STYLES: StyleOption[] = [
  { id: "", label: "Nenhum", suffix: "" },
  { id: "fotografia", label: "Fotografia realista", suffix: ", fotografia realista, hiper-detalhada, iluminação natural" },
  { id: "cinema", label: "Cinematográfico", suffix: ", estilo cinematográfico, cores dramáticas, profundidade de campo" },
  { id: "editorial", label: "Editorial de moda", suffix: ", editorial de moda, still de revista, alta produção" },
  { id: "pintura", label: "Pintura artística", suffix: ", pintura digital artística, pinceladas visíveis" },
];

interface Props {
  personas: PersonaSummary[];
  personaThumbnails: Record<string, string>;
  models: ModelInfo[];
  workflows: WorkflowInfo[];
  loading: boolean;
  prompt: string;
  onPromptChange: (prompt: string) => void;
  personaId: string;
  onPersonaChange: (personaId: string) => void;
  settings: GenerationSettings;
  onNavigatePersonas: () => void;
  chat: ChatSession;
  onSubmit: (params: {
    prompt: string;
    modelId: string;
    workflowId: string;
    personaId: string;
    width: number;
    height: number;
    steps: number;
    guidance: number;
  }) => void;
}

export function GeneratePanel({
  personas,
  personaThumbnails,
  models,
  workflows,
  loading,
  prompt,
  onPromptChange,
  personaId,
  onPersonaChange,
  settings,
  onNavigatePersonas,
  chat,
  onSubmit,
}: Props) {
  const [ambiente, setAmbiente] = useState("");
  const [formatId, setFormatId] = useState("16:9");
  const [styleId, setStyleId] = useState("fotografia");
  const [showChat, setShowChat] = useState(false);

  const format = FORMATS.find((f) => f.id === formatId) ?? FORMATS[0];
  const style = STYLES.find((s) => s.id === styleId) ?? STYLES[0];

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const finalPrompt = `${prompt.trim()}${ambiente.trim() ? `. Ambiente: ${ambiente.trim()}` : ""}${style.suffix}`;
    onSubmit({
      prompt: finalPrompt,
      // Ignora modelo/workflow salvo no navegador que nao existe mais no
      // backend (ex: FLUX Kontext depois da troca para o Chroma).
      modelId: models.some((m) => m.id === settings.modelId) ? settings.modelId : models[0]?.id,
      workflowId: workflows.some((w) => w.id === settings.workflowId) ? settings.workflowId : workflows[0]?.id,
      personaId,
      width: format.width,
      height: format.height,
      steps: settings.steps,
      guidance: settings.guidance,
    });
  }

  return (
    <form className="panel" onSubmit={handleSubmit}>
      <h2>Crie com IA</h2>
      <p className="muted small">Escolha uma persona, descreva o que deseja e gere imagens.</p>

      <div className="step-label">1. Escolha a persona</div>
      <div className="persona-picker">
        {personas.map((p) => (
          <button
            key={p.id}
            type="button"
            className={personaId === p.id ? "persona-card active" : "persona-card"}
            onClick={() => onPersonaChange(p.id)}
          >
            {personaThumbnails[p.id] && <img src={personaThumbnails[p.id]} alt={p.name} />}
            {personaId === p.id && <span className="persona-card-check">✓</span>}
            <span className="persona-card-label">
              {p.name}
              <span>{p.reference_count} referências</span>
            </span>
          </button>
        ))}
        <button type="button" className="persona-card add" onClick={onNavigatePersonas}>
          <span className="persona-card-plus">+</span>
          Nova Persona
        </button>
        <div className="persona-card soon">
          <span className="persona-card-plus">◔</span>
          Em breve
        </div>
      </div>

      <div className="step-label">2. Descreva o que deseja</div>
      <textarea
        rows={3}
        value={prompt}
        onChange={(e) => onPromptChange(e.target.value)}
        placeholder="Ex: Luna sentada em uma cafeteria tomando um café e olhando para a câmera, sorrindo de forma natural."
        required
        maxLength={1000}
      />
      <div className="char-count">{prompt.length}/1000</div>
      <div className="chat-toggle-row">
        <button type="button" className="chat-toggle-btn" onClick={() => setShowChat((v) => !v)}>
          {showChat ? "Fechar assistente de prompt" : "✨ Pedir ajuda da IA para montar o prompt"}
        </button>
      </div>
      {showChat && <ChatAssistant chat={chat} personas={personas} personaId={personaId} onUsePrompt={onPromptChange} />}

      <div className="step-label">3. Ambiente (opcional)</div>
      <textarea
        rows={2}
        value={ambiente}
        onChange={(e) => setAmbiente(e.target.value)}
        placeholder="Ex: Cafeteria moderna em São Paulo, manhã, luz natural entrando pelas janelas, clima descontraído e realista."
        maxLength={500}
      />
      <div className="char-count">{ambiente.length}/500</div>

      <div className="row" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div>
          <div className="step-label">4. Tipo de mídia</div>
          <div className="type-row">
            <button type="button" className="pick-btn active">
              Foto
            </button>
            <button type="button" className="pick-btn" disabled>
              Vídeo
              <small>Em breve</small>
            </button>
          </div>
        </div>

        <div>
          <div className="step-label">5. Formato</div>
          <div className="format-row">
            {FORMATS.map((f) => (
              <button
                key={f.id}
                type="button"
                className={formatId === f.id ? "pick-btn active" : "pick-btn"}
                onClick={() => setFormatId(f.id)}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="step-label">6. Estilo (opcional)</div>
      <select value={styleId} onChange={(e) => setStyleId(e.target.value)}>
        {STYLES.map((s) => (
          <option key={s.id} value={s.id}>
            {s.label}
          </option>
        ))}
      </select>

      <button type="submit" className="primary generate-submit" disabled={loading || !prompt.trim()}>
        {loading ? "Gerando..." : "✨ Gerar imagem"}
      </button>
    </form>
  );
}

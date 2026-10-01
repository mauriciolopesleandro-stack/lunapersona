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

// Sufixos em ingles: o encoder de texto (T5) entende ingles muito melhor.
const STYLES: StyleOption[] = [
  { id: "", label: "Nenhum", suffix: "" },
  { id: "fotografia", label: "Fotografia realista", suffix: ", candid smartphone photo, natural lighting, background in focus" },
  { id: "cinema", label: "Cinematográfico", suffix: ", cinematic film still, dramatic colors, shallow depth of field" },
  { id: "editorial", label: "Editorial de moda", suffix: ", fashion editorial, magazine photoshoot, high production" },
  { id: "pintura", label: "Pintura artística", suffix: ", artistic digital painting, visible brush strokes" },
];

export interface ReferenceImage {
  file: File;
  previewUrl: string;
  width: number;
  height: number;
}

// Tamanho de saida com a proporcao da foto, ~1 megapixel e multiplo de 16
// (o Chroma trabalha em blocos de 16 px).
// Abaixo disso (ex.: print de video 854x480) a geracao herda o borrado da foto.
const REFERENCE_MIN_PIXELS = 700_000;

function referenceOutputSize(width: number, height: number): { width: number; height: number } {
  const scale = Math.sqrt((1024 * 1024) / (width * height));
  const snap = (v: number) => Math.max(512, Math.min(1536, Math.round((v * scale) / 16) * 16));
  return { width: snap(width), height: snap(height) };
}

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
    // So o texto digitado (sem ambiente/estilo): e o que volta ao editar.
    displayPrompt: string;
    modelId: string;
    workflowId: string;
    personaId: string;
    width: number;
    height: number;
    steps: number;
    guidance: number;
    referenceFile?: File;
    denoise?: number;
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
  const [reference, setReference] = useState<ReferenceImage | null>(null);
  const [referenceError, setReferenceError] = useState<string | null>(null);
  // 85%: abaixo disso as cores grandes da foto (cabelo, olhos) costumam
  // continuar as da pessoa original em vez de virar as da persona.
  // 0.8: ainda troca cabelo/corpo pela persona, mas guarda mais da pose e da roupa.
  const [denoise, setDenoise] = useState(0.8);

  const format = FORMATS.find((f) => f.id === formatId) ?? FORMATS[0];
  const style = STYLES.find((s) => s.id === styleId) ?? STYLES[0];
  const outputSize = reference ? referenceOutputSize(reference.width, reference.height) : format;

  function handleReferenceChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setReferenceError(null);
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      if (reference) URL.revokeObjectURL(reference.previewUrl);
      setReference({ file, previewUrl: url, width: img.naturalWidth, height: img.naturalHeight });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      setReferenceError("Não consegui abrir essa imagem. Tente uma foto JPG ou PNG.");
    };
    img.src = url;
  }

  function removeReference() {
    if (reference) URL.revokeObjectURL(reference.previewUrl);
    setReference(null);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Com foto de referencia o texto e opcional: a cena vem da foto.
    const basePrompt = prompt.trim() || (reference ? "same scene, realistic photo" : "");
    const finalPrompt = `${basePrompt}${ambiente.trim() ? `. Ambiente: ${ambiente.trim()}` : ""}${style.suffix}`;
    onSubmit({
      prompt: finalPrompt,
      displayPrompt: basePrompt,
      // Ignora modelo/workflow salvo no navegador que nao existe mais no
      // backend (ex: FLUX Kontext depois da troca para o Chroma).
      modelId: models.some((m) => m.id === settings.modelId) ? settings.modelId : models[0]?.id,
      workflowId: workflows.some((w) => w.id === settings.workflowId) ? settings.workflowId : workflows[0]?.id,
      personaId,
      width: outputSize.width,
      height: outputSize.height,
      steps: settings.steps,
      guidance: settings.guidance,
      referenceFile: reference?.file,
      denoise: reference ? denoise : undefined,
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
        {/* Sem persona: o prompt vai para o modelo sem a identidade de ninguem. */}
        <button
          type="button"
          className={personaId === "" ? "persona-card none active" : "persona-card none"}
          onClick={() => onPersonaChange("")}
        >
          {personaId === "" && <span className="persona-card-check">✓</span>}
          <span className="persona-card-plus">∅</span>
          Sem persona
          <span className="persona-card-hint">só o que você descrever</span>
        </button>
        <button type="button" className="persona-card add" onClick={onNavigatePersonas}>
          <span className="persona-card-plus">+</span>
          Nova Persona
        </button>
        <div className="persona-card soon">
          <span className="persona-card-plus">◔</span>
          Em breve
        </div>
      </div>

      <div className="step-label">2. Foto de referência (opcional)</div>
      <p className="muted small reference-hint">
        Suba uma foto com a cena que você quer: a IA mantém o cenário, a luz e a pose e coloca a persona escolhida no lugar da
        pessoa.
      </p>
      {reference ? (
        <div className="reference-box">
          <img src={reference.previewUrl} alt="Foto de referência" />
          <div className="reference-controls">
            <label className="reference-denoise">
              <span>
                Quanto mudar: <strong>{Math.round(denoise * 100)}%</strong>
              </span>
              <input
                type="range"
                min={0.4}
                max={0.95}
                step={0.05}
                value={denoise}
                onChange={(e) => setDenoise(Number(e.target.value))}
              />
              <span className="reference-denoise-scale">
                <span>mais fiel à foto</span>
                <span>mais a persona</span>
              </span>
            </label>
            <button type="button" className="danger small" onClick={removeReference}>
              Remover foto
            </button>
          </div>
        </div>
      ) : (
        <label className="reference-upload">
          <input type="file" accept="image/png,image/jpeg,image/webp" onChange={handleReferenceChange} />
          📷 Escolher foto de referência
        </label>
      )}
      {referenceError && <p className="error small">{referenceError}</p>}
      {reference && reference.width * reference.height < REFERENCE_MIN_PIXELS && (
        <p className="warning small">
          Foto pequena ({reference.width}×{reference.height}): o borrado dela passa para o resultado. Prefira uma foto
          com pelo menos 1000 px no lado menor (print de vídeo costuma sair ruim).
        </p>
      )}

      <div className="step-label">3. Descreva o que deseja{reference ? " (opcional)" : ""}</div>
      <textarea
        rows={3}
        value={prompt}
        onChange={(e) => onPromptChange(e.target.value)}
        placeholder={
          reference
            ? "Descreva a pose e a roupa da foto com detalhes (a descrição automática não vê mãos e acabamentos). Ex: as duas mãos puxando as alças laterais da calcinha, sutiã de renda marrom com borda preta"
            : "Ex: Luna sentada em uma cafeteria tomando um café e olhando para a câmera, sorrindo de forma natural."
        }
        required={!reference}
        maxLength={1000}
      />
      <div className="char-count">{prompt.length}/1000</div>
      <div className="chat-toggle-row">
        <button type="button" className="chat-toggle-btn" onClick={() => setShowChat((v) => !v)}>
          {showChat ? "Fechar assistente de prompt" : "✨ Pedir ajuda da IA para montar o prompt"}
        </button>
      </div>
      {showChat && <ChatAssistant chat={chat} personas={personas} personaId={personaId} onUsePrompt={onPromptChange} />}

      <div className="step-label">4. Ambiente (opcional)</div>
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
          <div className="step-label">5. Tipo de mídia</div>
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
          <div className="step-label">6. Formato</div>
          {reference ? (
            <p className="muted small">
              Segue a proporção da foto ({outputSize.width}×{outputSize.height}).
            </p>
          ) : (
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
          )}
        </div>
      </div>

      <div className="step-label">7. Estilo (opcional)</div>
      <select value={styleId} onChange={(e) => setStyleId(e.target.value)}>
        {STYLES.map((s) => (
          <option key={s.id} value={s.id}>
            {s.label}
          </option>
        ))}
      </select>

      <button type="submit" className="primary generate-submit" disabled={loading || (!prompt.trim() && !reference)}>
        {loading ? "Gerando..." : reference ? "✨ Gerar imagem (com a foto de referência)" : "✨ Gerar imagem"}
      </button>
      {/* A foto de referencia continua valendo nas proximas geracoes ate ser
          removida - sem esse aviso parecia que o site "pegava a foto anterior". */}
      {reference && !loading && (
        <p className="muted small">
          A foto de referência do passo 2 continua sendo usada. Para gerar sem ela, clique em remover lá em cima.
        </p>
      )}
    </form>
  );
}

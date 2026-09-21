import { useState } from "react";
import type { ModelInfo, WorkflowInfo } from "../api/client";

interface Props {
  models: ModelInfo[];
  workflows: WorkflowInfo[];
  loading: boolean;
  onSubmit: (params: {
    prompt: string;
    modelId: string;
    workflowId: string;
    width: number;
    height: number;
    steps: number;
    guidance: number;
  }) => void;
}

export function GenerationForm({ models, workflows, loading, onSubmit }: Props) {
  const [prompt, setPrompt] = useState("");
  const [modelId, setModelId] = useState(models[0]?.id ?? "");
  const [workflowId, setWorkflowId] = useState(workflows[0]?.id ?? "");
  const [width, setWidth] = useState(1024);
  const [height, setHeight] = useState(1024);
  const [steps, setSteps] = useState(20);
  const [guidance, setGuidance] = useState(2.5);

  return (
    <form
      className="panel"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({
          prompt,
          modelId: modelId || models[0]?.id,
          workflowId: workflowId || workflows[0]?.id,
          width,
          height,
          steps,
          guidance,
        });
      }}
    >
      <h2>Geração</h2>

      <label htmlFor="prompt">Prompt</label>
      <textarea
        id="prompt"
        rows={4}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="Descreva a imagem que deseja gerar..."
        required
      />

      <div className="row">
        <div>
          <label htmlFor="model">Modelo</label>
          <select id="model" value={modelId} onChange={(e) => setModelId(e.target.value)}>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="workflow">Workflow</label>
          <select id="workflow" value={workflowId} onChange={(e) => setWorkflowId(e.target.value)}>
            {workflows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.title}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="row">
        <div>
          <label htmlFor="width">Largura</label>
          <input id="width" type="number" value={width} onChange={(e) => setWidth(Number(e.target.value))} />
        </div>
        <div>
          <label htmlFor="height">Altura</label>
          <input id="height" type="number" value={height} onChange={(e) => setHeight(Number(e.target.value))} />
        </div>
        <div>
          <label htmlFor="steps">Steps</label>
          <input id="steps" type="number" value={steps} onChange={(e) => setSteps(Number(e.target.value))} />
        </div>
        <div>
          <label htmlFor="guidance">Guidance</label>
          <input
            id="guidance"
            type="number"
            step="0.1"
            value={guidance}
            onChange={(e) => setGuidance(Number(e.target.value))}
          />
        </div>
      </div>

      <button type="submit" disabled={loading || !prompt.trim()}>
        {loading ? "Gerando..." : "Gerar imagem"}
      </button>
    </form>
  );
}

import { useEffect, useState } from "react";
import {
  getEnginesCatalog,
  getPersonaAttributes,
  type AttributePolicyValue,
  type PersonaAttributes,
  runFaceSwap,
  runReplacement,
  type EngineV2Mode,
  type EngineV2Result,
  type EnginesCatalog,
  type FaceSwapMode,
  type PersonaSummary,
} from "../api/client";
import { downloadFile } from "../lib/download";
import { PersonaEngineGenerate } from "./PersonaEngineGenerate";

interface Props {
  personas: PersonaSummary[];
  ensureAwake: () => Promise<unknown>;
}

type Engine = "generation" | "replacement" | "face_swap";

const ENGINE_LABEL: Record<Engine, string> = {
  generation: "Geração",
  replacement: "Substituição (pessoa da foto vira a persona)",
  face_swap: "Troca de rosto",
};

const MODE_LABEL: Record<EngineV2Mode, string> = {
  FAST: "Rápido",
  QUALITY: "Qualidade",
  MAX_QUALITY: "Qualidade máxima",
};

const FACE_LABEL: Record<FaceSwapMode, string> = {
  FACE_ONLY: "Só o rosto",
  FACE_NECK: "Rosto + pescoço",
  FACE_INTEGRATED: "Rosto integrado (cabelo e pele)",
  FULL_PERSON: "Pessoa inteira (usa a Substituição)",
};

const OPTION_LABEL: Record<string, string> = {
  preserve_pose: "Manter a pose",
  preserve_clothes: "Manter a roupa",
  preserve_background: "Manter o cenário",
  preserve_lighting: "Manter a luz",
  remove_original_tattoos: "Tirar as tatuagens da pessoa original",
  identity_lock: "Travar a identidade (referência do rosto)",
  body_lock: "Refinar o corpo",
  skin_realism: "Pele realista",
  photographic_integration: "Integração fotográfica",
};

// Fundo e roupa ficam sempre (o Replacement nao regenera); o resto pode ser desligado.
const FIXED_OPTIONS = new Set(["preserve_background", "preserve_clothes"]);
// Tatuagens agora sao um ATRIBUTO (secao abaixo), nao uma opcao solta.
const HIDDEN_OPTIONS = new Set(["remove_original_tattoos"]);

const ATTR_LABEL: Record<string, string> = {
  pose: "Pose",
  composition: "Enquadramento",
  camera_angle: "Ângulo da câmera",
  perspective: "Perspectiva",
  clothing: "Roupa",
  background: "Cenário",
  lighting: "Luz",
  objects: "Objetos",
  expression: "Expressão",
  accessories: "Acessórios (óculos, boné)",
  face: "Rosto",
  body: "Corpo",
  skin: "Pele",
  hair: "Cabelo",
  age: "Idade",
  tattoos: "Tatuagens",
  scars: "Cicatrizes",
  piercings: "Piercings",
  birthmarks: "Pintas/marcas de nascença",
  makeup: "Maquiagem",
  jewelry: "Joias (brincos, colares)",
  original_person_marks: "Outras marcas da pessoa original",
};

const POLICY_LABEL: Record<string, string> = {
  PRESERVE: "Manter da foto",
  RECONSTRUCT: "Da persona",
  REMOVE: "Tirar",
  OPTIONAL: "Tanto faz",
  IGNORE: "Ignorar",
};

const ADVANCED_LABEL: Record<string, [string, number, number, number]> = {
  steps: ["Passos", 10, 60, 1],
  cfg: ["CFG", 1, 10, 0.5],
  identity_denoise: ["Denoise da identidade", 0.3, 1, 0.05],
  face_denoise: ["Denoise do rosto", 0.1, 0.8, 0.05],
  face_reference_strength: ["Força da referência do rosto", 0, 0.6, 0.05],
  pose_strength: ["Força da pose", 0, 1.2, 0.05],
  depth_strength: ["Força da profundidade", 0, 1.2, 0.05],
  tattoo_denoise: ["Denoise da tatuagem", 0.2, 0.95, 0.05],
  body_denoise: ["Denoise do corpo", 0.1, 0.7, 0.05],
  max_retries: ["Novas tentativas", 0, 3, 1],
};

const CHECK_LABEL: Record<string, string> = {
  identity: "Identidade",
  original_residual: "Rosto original sobrando",
  tattoo: "Tatuagem",
  hair_residual: "Cabelo original",
  pose: "Pose",
  body: "Corpo",
  anatomy: "Anatomia",
  skin: "Pele",
  duplicate_persona: "Persona duplicada",
  background: "Cenário",
  composition: "Enquadramento",
  seams: "Emendas e blocos",
  attribute_policy: "Política de atributos",
};

function errorText(e: unknown) {
  return String(e instanceof Error ? e.message : e);
}

// score/limite podem vir como numero, faixa [min, max] ou texto (ex.: pele tem limite baixo e alto).
function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Math.abs(v) < 1 ? v.toFixed(3) : v.toFixed(1);
  if (Array.isArray(v)) return v.map(fmt).join(" – ");
  if (typeof v === "object") return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k} ${fmt(x)}`).join(", ");
  return String(v);
}

// Persona V2: tres engines independentes. Geracao = a V1 exatamente como esta; Substituicao e Troca de
// rosto chamam /api/v2. Toda decisao (mascaras, passes, validacao, nova tentativa) e do backend.
export function EnginesV2Page({ personas, ensureAwake }: Props) {
  const [catalog, setCatalog] = useState<EnginesCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [engine, setEngine] = useState<Engine>("replacement");
  const [mode, setMode] = useState<EngineV2Mode>("QUALITY");
  const [model, setModel] = useState("auto");
  const [faceMode, setFaceMode] = useState<FaceSwapMode>("FACE_INTEGRATED");
  const [personaId, setPersonaId] = useState(personas[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [options, setOptions] = useState<Record<string, boolean>>({});
  const [advanced, setAdvanced] = useState<Record<string, number>>({});
  const [seed, setSeed] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EngineV2Result | null>(null);
  // politica de atributos (spec 45): padrao da persona + o que o usuario mudar (vai explicito para o backend)
  const [personaAttrs, setPersonaAttrs] = useState<PersonaAttributes | null>(null);
  const [attrChoice, setAttrChoice] = useState<Record<string, AttributePolicyValue>>({});
  const [keepItems, setKeepItems] = useState("");
  const [removeItems, setRemoveItems] = useState("");

  async function loadCatalog() {
    setCatalogError(null);
    try {
      await ensureAwake();
      const c = await getEnginesCatalog();
      setCatalog(c);
      setOptions(Object.fromEntries(c.replacement.options.map((o) => [o, true])));
    } catch (e) {
      setCatalogError(errorText(e));
    }
  }

  useEffect(() => {
    if (engine !== "generation" && !catalog) void loadCatalog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine]);

  useEffect(() => {
    if (!personaId && personas[0]) setPersonaId(personas[0].id);
  }, [personas, personaId]);

  useEffect(() => {
    if (engine !== "replacement" || !catalog || !personaId) return;
    setPersonaAttrs(null);
    setAttrChoice({});
    getPersonaAttributes(personaId)
      .then(setPersonaAttrs)
      .catch(() => setPersonaAttrs(null));
  }, [engine, catalog, personaId]);

  // So vai como pedido explicito o que o usuario MUDOU em relacao ao padrao da persona (+ itens livres).
  function attributeLists() {
    const lists = { preserve: [] as string[], remove: [] as string[], reconstruct: [] as string[] };
    for (const [attr, pol] of Object.entries(attrChoice)) {
      if (personaAttrs && personaAttrs.policy[attr] === pol) continue;
      if (pol === "PRESERVE") lists.preserve.push(attr);
      else if (pol === "REMOVE") lists.remove.push(attr);
      else if (pol === "RECONSTRUCT") lists.reconstruct.push(attr);
    }
    const split = (t: string) => t.split(",").map((x) => x.trim()).filter(Boolean);
    lists.preserve.push(...split(keepItems));
    lists.remove.push(...split(removeItems));
    return lists;
  }

  useEffect(() => {
    if (!file) return setPreview(null);
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const persona = personas.find((p) => p.id === personaId);
  const chosenModel = catalog?.models.find((m) => m.id === model);
  const modelBlocked = chosenModel && chosenModel.status !== "AVAILABLE";

  async function handleRun() {
    if (!file || !personaId) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      await ensureAwake();
      const s = seed.trim() === "" ? undefined : Number(seed);
      const out =
        engine === "replacement"
          ? await runReplacement({ file, personaId, mode, model, seed: s, options, advanced, ...attributeLists() })
          : await runFaceSwap({ file, personaId, mode: faceMode, model, seed: s, replacementMode: mode });
      setResult(out);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="panel">
      <div className="page-header">
        <h1>Persona V2</h1>
        <p>Três engines separadas. Nenhuma troca de modelo é automática e a força da LoRA nunca sobe sozinha.</p>
      </div>

      <div className="identity-grid">
        <div>
          <label htmlFor="v2-engine">Engine</label>
          <select id="v2-engine" value={engine} onChange={(e) => setEngine(e.target.value as Engine)}>
            {(Object.keys(ENGINE_LABEL) as Engine[]).map((k) => (
              <option key={k} value={k}>
                {ENGINE_LABEL[k]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="v2-persona">Persona</label>
          <select id="v2-persona" value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        {engine !== "generation" && (
          <>
            <div>
              <label htmlFor="v2-mode">Modo</label>
              <select id="v2-mode" value={mode} onChange={(e) => setMode(e.target.value as EngineV2Mode)}>
                {(Object.keys(MODE_LABEL) as EngineV2Mode[]).map((k) => (
                  <option key={k} value={k}>
                    {MODE_LABEL[k]}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="v2-model">Modelo</label>
              <select id="v2-model" value={model} onChange={(e) => setModel(e.target.value)}>
                {(catalog?.models ?? [{ id: "auto", title: "Auto", status: "AVAILABLE", default: true }]).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.title}
                    {m.status !== "AVAILABLE" ? " (indisponível)" : ""}
                  </option>
                ))}
              </select>
            </div>
          </>
        )}
      </div>

      {engine === "generation" ? (
        persona ? (
          <PersonaEngineGenerate personaId={persona.id} personaName={persona.name} ensureAwake={ensureAwake} />
        ) : (
          <p className="muted small">Escolha uma persona.</p>
        )
      ) : (
        <>
          {catalogError && (
            <p className="error small">
              Não carreguei as engines V2: {catalogError}{" "}
              <button type="button" className="small" onClick={() => void loadCatalog()}>
                Tentar de novo
              </button>
            </p>
          )}
          {modelBlocked && (
            <p className="error small">
              {chosenModel.title} não está disponível ({chosenModel.status}
              {chosenModel.download_auth ? `, precisa de ${chosenModel.download_auth}` : ""}). Não há troca automática para
              outro modelo.
              {chosenModel.license_status === "LICENSE_REVIEW_REQUIRED" ? " Licença em revisão." : ""}
            </p>
          )}

          <label htmlFor="v2-file">Foto</label>
          <input
            id="v2-file"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          {preview && <img className="v2-preview" src={preview} alt="Foto escolhida" />}

          {engine === "face_swap" && (
            <div>
              <label htmlFor="v2-face-mode">O que trocar</label>
              <select id="v2-face-mode" value={faceMode} onChange={(e) => setFaceMode(e.target.value as FaceSwapMode)}>
                {(Object.keys(FACE_LABEL) as FaceSwapMode[]).map((k) => (
                  <option key={k} value={k}>
                    {FACE_LABEL[k]}
                  </option>
                ))}
              </select>
            </div>
          )}

          {engine === "replacement" && catalog && (
            <fieldset className="v2-options">
              <legend>Opções</legend>
              {catalog.replacement.options.filter((o) => !HIDDEN_OPTIONS.has(o)).map((o) => (
                <label key={o} className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={FIXED_OPTIONS.has(o) ? true : options[o] ?? true}
                    disabled={FIXED_OPTIONS.has(o)}
                    onChange={(e) => setOptions({ ...options, [o]: e.target.checked })}
                  />
                  {OPTION_LABEL[o] ?? o}
                  {FIXED_OPTIONS.has(o) ? " (sempre)" : ""}
                </label>
              ))}
            </fieldset>
          )}

          {engine === "replacement" && catalog && (
            <fieldset className="v2-options">
              <legend>O que fica da foto e o que vem da persona</legend>
              <p className="muted small">
                A foto só manda no que estiver em “Manter da foto”. Rosto, pele e corpo vêm da Persona Sheet. Marcas da
                pessoa original (tatuagem, cicatriz) saem e viram pele da persona.
              </p>
              {!personaAttrs && <p className="muted small">Carregando a política da persona...</p>}
              {personaAttrs && (
                <>
                  <p className="muted small">
                    Sempre da foto:{" "}
                    {catalog.replacement.attributes
                      .filter((a) => a.allowed.length === 1 && a.allowed[0] === "PRESERVE")
                      .map((a) => ATTR_LABEL[a.attribute] ?? a.attribute)
                      .join(", ")}
                    . Sempre da persona:{" "}
                    {catalog.replacement.attributes
                      .filter((a) => a.allowed.length === 1 && a.allowed[0] === "RECONSTRUCT")
                      .map((a) => ATTR_LABEL[a.attribute] ?? a.attribute)
                      .join(", ")}
                    .
                  </p>
                  <div className="identity-grid">
                    {catalog.replacement.attributes
                      .filter((a) => a.allowed.length > 1)
                      .map((a) => {
                        const value = attrChoice[a.attribute] ?? personaAttrs.policy[a.attribute] ?? a.default;
                        const changed = attrChoice[a.attribute] !== undefined && attrChoice[a.attribute] !== personaAttrs.policy[a.attribute];
                        return (
                          <div key={a.attribute}>
                            <label htmlFor={`v2-attr-${a.attribute}`}>
                              {ATTR_LABEL[a.attribute] ?? a.attribute}
                              {changed ? " (pedido seu)" : ""}
                            </label>
                            <select
                              id={`v2-attr-${a.attribute}`}
                              value={value}
                              onChange={(e) => setAttrChoice({ ...attrChoice, [a.attribute]: e.target.value as AttributePolicyValue })}
                            >
                              {a.allowed.map((p) => (
                                <option key={p} value={p}>
                                  {POLICY_LABEL[p] ?? p}
                                </option>
                              ))}
                            </select>
                          </div>
                        );
                      })}
                    <div>
                      <label htmlFor="v2-keep-items">Manter também (itens, separados por vírgula)</label>
                      <input id="v2-keep-items" value={keepItems} placeholder="top preto, bolsa" onChange={(e) => setKeepItems(e.target.value)} />
                    </div>
                    <div>
                      <label htmlFor="v2-remove-items">Tirar também</label>
                      <input id="v2-remove-items" value={removeItems} placeholder="adesivo no braço" onChange={(e) => setRemoveItems(e.target.value)} />
                    </div>
                  </div>
                </>
              )}
            </fieldset>
          )}

          {catalog && (
            <details className="v2-advanced">
              <summary>Configurações avançadas</summary>
              <p className="muted small">
                Vazio = o padrão do modo. A força da LoRA não aparece aqui de propósito: fica sempre a do registro.
              </p>
              <div className="identity-grid">
                <div>
                  <label htmlFor="v2-seed">Seed</label>
                  <input id="v2-seed" inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ""))} />
                </div>
                {engine === "replacement" &&
                  catalog.replacement.advanced.map((k) => {
                    const [label, min, max, step] = ADVANCED_LABEL[k] ?? [k, 0, 100, 1];
                    const def = catalog.replacement.defaults[mode]?.[k];
                    return (
                      <div key={k}>
                        <label htmlFor={`v2-adv-${k}`}>{label}</label>
                        <input
                          id={`v2-adv-${k}`}
                          type="number"
                          min={min}
                          max={max}
                          step={step}
                          placeholder={def === undefined ? "" : String(def)}
                          value={advanced[k] ?? ""}
                          onChange={(e) => {
                            const next = { ...advanced };
                            if (e.target.value === "") delete next[k];
                            else next[k] = Number(e.target.value);
                            setAdvanced(next);
                          }}
                        />
                      </div>
                    );
                  })}
              </div>
            </details>
          )}

          <button type="button" className="primary" onClick={handleRun} disabled={running || !file || !personaId || !!modelBlocked || !catalog}>
            {running ? "Processando na GPU..." : engine === "replacement" ? "Substituir pela persona" : "Trocar o rosto"}
          </button>
          {error && <p className="error small">{error}</p>}

          {result && (
            <div className="engine-job">
              <p className={result.status === "PASS" ? "saved-hint" : result.status === "WARN" ? "muted" : "error small"}>
                {result.status === "PASS" ? "Aprovada" : result.status === "WARN" ? "Aprovada com avisos" : "Reprovada"} ·{" "}
                {result.model} · {result.mode}
              </p>
              <div className="v2-compare">
                {preview && <img src={preview} alt="Original" />}
                <img src={result.image_url} alt="Resultado" />
              </div>
              <button type="button" className="small" onClick={() => downloadFile(result.image_url, `persona_v2_${result.engine}.png`)}>
                Baixar
              </button>
              <table className="v2-checks">
                <thead>
                  <tr>
                    <th>Conferência</th>
                    <th>Resultado</th>
                    <th>Valor</th>
                    <th>Limite</th>
                    <th>Motivo</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.values(result.validation.checks).map((c) => (
                    <tr key={c.name} className={`v2-${c.status.toLowerCase()}`}>
                      <td>{CHECK_LABEL[c.name] ?? c.name}</td>
                      <td>{c.status}</td>
                      <td>{fmt(c.score)}</td>
                      <td>{fmt(c.threshold)}</td>
                      <td>{c.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small">
                {String(result.telemetry.duration_s ?? "—")} s · GPU {String(result.telemetry.gpu_seconds ?? "—")} s · custo
                estimado US$ {String(result.telemetry.estimated_cost_usd ?? "—")} · novas tentativas{" "}
                {String(result.telemetry.retry_count ?? 0)}
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

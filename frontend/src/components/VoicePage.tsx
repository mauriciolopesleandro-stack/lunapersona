import { useEffect, useState } from "react";
import {
  designVoice,
  getPersonaVoice,
  personaVoiceFileUrl,
  savePersonaVoice,
  speakWithVoice,
  type GenerationImage,
  type PersonaSummary,
  type PersonaVoice,
  type VoiceDesignResponse,
} from "../api/client";
import { downloadFile } from "../lib/download";

interface Props {
  personas: PersonaSummary[];
  // Liga o pod se estiver desligado (a voz roda no ComfyUI dele).
  ensureAwake: () => Promise<unknown>;
}

const DEFAULT_DESCRIPTION =
  "Voz feminina jovem, brasileira, uns 25 anos, suave e um pouco rouca, tom descontraído e confiante, sorrindo ao falar";

function errorText(e: unknown) {
  return String(e instanceof Error ? e.message : e);
}

// Aba Voz: cria a voz da persona (3 opcoes por descricao, escolhe uma) e
// depois faz a persona falar qualquer texto sempre com essa mesma voz.
export function VoicePage({ personas, ensureAwake }: Props) {
  const [personaId, setPersonaId] = useState(personas[0]?.id ?? "");
  const [voice, setVoice] = useState<PersonaVoice | null>(null);
  const [voiceUrl, setVoiceUrl] = useState("");
  const [description, setDescription] = useState(DEFAULT_DESCRIPTION);
  const [sampleText, setSampleText] = useState("");
  const [design, setDesign] = useState<VoiceDesignResponse | null>(null);
  const [speech, setSpeech] = useState("");
  const [spoken, setSpoken] = useState<GenerationImage | null>(null);
  const [busy, setBusy] = useState<"design" | "save" | "speak" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!personaId && personas[0]) setPersonaId(personas[0].id);
  }, [personas, personaId]);

  async function loadVoice(id: string) {
    const v = await getPersonaVoice(id);
    setVoice(v);
    // ?v= troca a URL quando a voz muda, senao o navegador toca a antiga.
    setVoiceUrl(v ? `${await personaVoiceFileUrl(id)}?v=${Date.now()}` : "");
  }

  useEffect(() => {
    if (!personaId) return;
    setDesign(null);
    setSpoken(null);
    loadVoice(personaId).catch(() => setVoice(null));
  }, [personaId]);

  async function run(kind: "design" | "save" | "speak", work: () => Promise<void>) {
    setBusy(kind);
    setError(null);
    try {
      await ensureAwake();
      await work();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const personaName = personas.find((p) => p.id === personaId)?.name ?? "a persona";

  return (
    <div className="voice-page">
      <div className="panel">
        <h2>Voz da persona</h2>
        <label className="voice-field">
          Persona
          <select value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        {voice ? (
          <div className="voice-current">
            <p className="small">
              Voz atual de {personaName}: <em>{voice.description || "sem descrição"}</em>
            </p>
            <audio src={voiceUrl} controls />
          </div>
        ) : (
          <p className="muted small">{personaName} ainda não tem voz. Crie uma abaixo.</p>
        )}
      </div>

      <div className="panel">
        <h3>1. Criar a voz</h3>
        <p className="muted small">
          Descreva a voz. O sistema cria 3 opções falando a mesma frase; a que você escolher vira a voz oficial.
        </p>
        <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        <input
          type="text"
          value={sampleText}
          onChange={(e) => setSampleText(e.target.value)}
          placeholder="Frase de teste (opcional). Ex: Oi, eu sou a Luna! Que bom te ver por aqui."
        />
        <button
          type="button"
          className="primary"
          disabled={busy !== null || description.trim().length < 3}
          onClick={() => run("design", async () => setDesign(await designVoice(description, sampleText)))}
        >
          {busy === "design" ? "Criando 3 opções..." : "Criar 3 opções de voz"}
        </button>
        {busy === "design" && <p className="muted small">Leva de 1 a 3 minutos (a primeira vez carrega o modelo).</p>}
        {design && (
          <div className="voice-options">
            {design.options.map((opt, i) => (
              <div key={opt.filename} className="voice-option">
                <strong>Opção {i + 1}</strong>
                <audio src={opt.url} controls />
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() =>
                    run("save", async () => {
                      await savePersonaVoice(personaId, {
                        filename: opt.filename,
                        subfolder: opt.subfolder,
                        text: design.text,
                        description,
                      });
                      await loadVoice(personaId);
                    })
                  }
                >
                  {busy === "save" ? "Salvando..." : "Usar esta voz"}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="panel">
        <h3>2. Fazer {personaName} falar</h3>
        <textarea
          rows={4}
          value={speech}
          onChange={(e) => setSpeech(e.target.value)}
          placeholder="O que ela vai falar. Ex: Gente, acabei de chegar na praia e o dia está perfeito!"
          disabled={!voice}
        />
        <button
          type="button"
          className="primary"
          disabled={busy !== null || !voice || !speech.trim()}
          onClick={() =>
            run("speak", async () => {
              const res = await speakWithVoice(personaId, speech);
              setSpoken(res.audios[0] ?? null);
            })
          }
        >
          {busy === "speak" ? "Gerando fala..." : "Gerar fala"}
        </button>
        {!voice && <p className="muted small">Crie e escolha uma voz primeiro.</p>}
        {spoken && (
          <div className="voice-current">
            <audio src={spoken.url} controls autoPlay />
            <button type="button" onClick={() => downloadFile(spoken.url, spoken.filename)}>
              ⬇ Baixar áudio
            </button>
          </div>
        )}
      </div>

      {error && <p className="error">{error}</p>}
    </div>
  );
}

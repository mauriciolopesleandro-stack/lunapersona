import { useEffect, useRef, useState } from "react";
import {
  clearContentConversation,
  deleteContentMemory,
  getContentConversation,
  getContentMemory,
  getContentProfile,
  saveContentProfile,
  sendContentMessage,
  speakWithVoice,
  type ContentMemory,
  type ContentMessage,
  type ContentProfile,
  type GenerationImage,
  type PersonaSummary,
} from "../api/client";
import { downloadFile } from "../lib/download";

interface Props {
  personas: PersonaSummary[];
  ensureAwake: () => Promise<unknown>;
  // Botao FOTO: leva o pedido para a aba Gerar com a persona escolhida.
  onUsePhoto: (personaId: string, prompt: string) => void;
}

const PROFILE_FIELDS: { key: keyof ContentProfile; label: string; rows: number }[] = [
  { key: "bio", label: "Quem ela é", rows: 3 },
  { key: "personalidade", label: "Personalidade", rows: 2 },
  { key: "jeito_de_falar", label: "Jeito de falar", rows: 2 },
  { key: "publico", label: "Público", rows: 1 },
  { key: "redes", label: "Redes", rows: 1 },
  { key: "nicho", label: "Nicho", rows: 2 },
  { key: "limites", label: "Limites (sempre respeitados)", rows: 2 },
];

const SUGGESTIONS = [
  "Me dá 5 ideias de post para essa semana",
  "Monta um roteiro de reels de 20 segundos",
  "Cria uma legenda provocante para uma foto na praia",
];

type Line = { kind: "text" | "FOTO" | "VIDEO" | "FALA" | "LEGENDA" | "MEMORIA"; text: string };

const ACTION = /^\s*(?:[-*\d.)\s]*)?\**\s*(FOTO|VIDEO|VÍDEO|FALA|LEGENDA|MEMORIA|MEMÓRIA)\s*\**\s*:\s*\**\s*(.+)$/i;

function parseReply(content: string): Line[] {
  return content.split("\n").map((raw) => {
    const m = raw.match(ACTION);
    if (!m) return { kind: "text", text: raw };
    const kind = m[1].toUpperCase().replace("Í", "I").replace("Ó", "O") as Line["kind"];
    return { kind, text: m[2].replace(/\*+$/, "").trim() };
  });
}

function errorText(e: unknown) {
  return String(e instanceof Error ? e.message : e);
}

// Aba Conteudo: conversa com a estrategista da persona (ideias, roteiros,
// falas, legendas). Ela lembra o que aprende e as sugestoes viram botoes.
export function ContentPage({ personas, ensureAwake, onUsePhoto }: Props) {
  const [personaId, setPersonaId] = useState(personas[0]?.id ?? "");
  const [messages, setMessages] = useState<ContentMessage[]>([]);
  const [memory, setMemory] = useState<ContentMemory[]>([]);
  const [profile, setProfile] = useState<ContentProfile | null>(null);
  const [showProfile, setShowProfile] = useState(false);
  const [showMemory, setShowMemory] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [audios, setAudios] = useState<Record<string, GenerationImage>>({});
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!personaId && personas[0]) setPersonaId(personas[0].id);
  }, [personas, personaId]);

  useEffect(() => {
    if (!personaId) return;
    setAudios({});
    Promise.all([getContentConversation(personaId), getContentMemory(personaId), getContentProfile(personaId)])
      .then(([conv, mem, prof]) => {
        setMessages(conv);
        setMemory(mem);
        setProfile(prof);
      })
      .catch(() => undefined);
  }, [personaId]);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 4000);
    return () => clearTimeout(t);
  }, [notice]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, busy]);

  async function run(kind: string, work: () => Promise<void>) {
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

  function send(message: string) {
    const clean = message.trim();
    if (!clean || busy) return;
    setText("");
    setMessages((m) => [...m, { role: "user", content: clean, at: Date.now() / 1000 }]);
    run("send", async () => {
      const res = await sendContentMessage(personaId, clean);
      setMessages((m) => [...m, { role: "assistant", content: res.reply, at: Date.now() / 1000 }]);
      if (res.memory_added.length) {
        setMemory(await getContentMemory(personaId));
        setNotice(`Anotado na memória: ${res.memory_added.join("; ")}`);
      }
    });
  }

  function copy(value: string) {
    navigator.clipboard?.writeText(value).then(
      () => setNotice("Copiado!"),
      () => setNotice("Não consegui copiar - selecione o texto e copie.")
    );
  }

  function speak(key: string, value: string) {
    run(`fala-${key}`, async () => {
      // A voz leria hashtags e emojis em voz alta.
      const spoken = value
        .replace(/#[p{L}p{N}_]+/gu, "")
        .replace(/[p{Extended_Pictographic}️‍]/gu, "")
        .replace(/^["“'s]+|["”'s]+$/g, "")
        .replace(/s{2,}/g, " ")
        .trim();
      const res = await speakWithVoice(personaId, spoken);
      if (res.audios[0]) setAudios((a) => ({ ...a, [key]: res.audios[0] }));
    });
  }

  const personaName = personas.find((p) => p.id === personaId)?.name ?? "a persona";

  return (
    <div className="content-page">
      <div className="panel content-header">
        <div>
          <h2>Conteúdo</h2>
          <p className="muted small">
            Converse sobre as redes de {personaName}: ideias, roteiros, falas e legendas. Ela aprende com a conversa.
          </p>
        </div>
        <div className="content-header-actions">
          {personas.length > 1 && (
            <select value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          )}
          <button type="button" onClick={() => setShowProfile((v) => !v)}>
            Manual
          </button>
          <button type="button" onClick={() => setShowMemory((v) => !v)}>
            Memória ({memory.length})
          </button>
          <button
            type="button"
            disabled={busy !== null || messages.length === 0}
            onClick={() =>
              run("clear", async () => {
                await clearContentConversation(personaId);
                setMessages([]);
                setAudios({});
              })
            }
          >
            Nova conversa
          </button>
        </div>
      </div>

      {showProfile && profile && (
        <div className="panel content-profile">
          <h3>Manual de {personaName}</h3>
          {PROFILE_FIELDS.map((f) => (
            <label key={f.key} className="voice-field">
              {f.label}
              <textarea
                rows={f.rows}
                value={profile[f.key]}
                onChange={(e) => setProfile({ ...profile, [f.key]: e.target.value })}
              />
            </label>
          ))}
          <button
            type="button"
            className="primary"
            disabled={busy !== null}
            onClick={() =>
              run("profile", async () => {
                setProfile(await saveContentProfile(personaId, profile));
                setNotice("Manual salvo.");
              })
            }
          >
            {busy === "profile" ? "Salvando..." : "Salvar manual"}
          </button>
        </div>
      )}

      {showMemory && (
        <div className="panel content-memory">
          <h3>O que ela já aprendeu</h3>
          {memory.length === 0 && <p className="muted small">Nada ainda. Conte sobre o nicho, o estilo, o que funciona.</p>}
          {memory.map((m, i) => (
            <div key={`${m.at}-${i}`} className="content-memory-item">
              <span>{m.text}</span>
              <button
                type="button"
                title="Esquecer"
                disabled={busy !== null}
                onClick={() => run("memory", async () => setMemory(await deleteContentMemory(personaId, i)))}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="panel content-chat">
        {messages.length === 0 && (
          <div className="content-empty">
            <p className="muted small">Comece contando o que você quer para as redes, ou tente:</p>
            {SUGGESTIONS.map((s) => (
              <button key={s} type="button" onClick={() => send(s)} disabled={busy !== null}>
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((msg, mi) =>
          msg.role === "user" ? (
            <div key={mi} className="content-msg user">
              {msg.content}
            </div>
          ) : (
            <div key={mi} className="content-msg assistant">
              {parseReply(msg.content).map((line, li) => {
                const key = `${mi}-${li}`;
                if (line.kind === "text") return line.text.trim() ? <p key={key}>{line.text}</p> : null;
                if (line.kind === "MEMORIA")
                  return (
                    <p key={key} className="content-memo">
                      📌 {line.text}
                    </p>
                  );
                return (
                  <div key={key} className={`content-action ${line.kind.toLowerCase()}`}>
                    <span className="content-action-tag">{line.kind}</span>
                    <span className="content-action-text">{line.text}</span>
                    <div className="content-action-buttons">
                      {line.kind === "FOTO" && (
                        <button type="button" className="primary" onClick={() => onUsePhoto(personaId, line.text)}>
                          Gerar foto
                        </button>
                      )}
                      {line.kind === "FALA" && (
                        <button
                          type="button"
                          className="primary"
                          disabled={busy !== null}
                          onClick={() => speak(key, line.text)}
                        >
                          {busy === `fala-${key}` ? "Gerando voz..." : "Ouvir com a voz dela"}
                        </button>
                      )}
                      <button type="button" onClick={() => copy(line.text)}>
                        Copiar
                      </button>
                    </div>
                    {audios[key] && (
                      <div className="voice-current">
                        <audio src={audios[key].url} controls autoPlay />
                        <button type="button" onClick={() => downloadFile(audios[key].url, audios[key].filename)}>
                          ⬇ Baixar áudio
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )
        )}
        {busy === "send" && <div className="content-msg assistant muted">Pensando...</div>}
        <div ref={endRef} />
      </div>

      {notice && (
        <p className="page-toast" onClick={() => setNotice(null)}>
          {notice}
        </p>
      )}
      {error && <p className="error">{error}</p>}

      <form
        className="panel content-input"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <textarea
          rows={3}
          value={text}
          placeholder={`Fale com a estrategista de ${personaName}...`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(text);
            }
          }}
        />
        <button type="submit" className="primary" disabled={busy !== null || !text.trim()}>
          {busy === "send" ? "Enviando..." : "Enviar"}
        </button>
      </form>
    </div>
  );
}

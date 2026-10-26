import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { PlanPanel } from "./plans";

// Assistente: chat do administrador com a IA que conhece a Luna (OpenRouter,
// pelo servidor). Duas conversas separadas: redes (Instagram/TikTok) e +18
// (so Telegram). A conversa fica guardada neste navegador.

type Mode = "normal" | "adulto";
interface Message {
  role: "user" | "assistant";
  content: string;
}
interface Status {
  configured: boolean;
  model: string;
  adultModel: string;
}

const STORE_KEY = "luna-assistente-v1";
const QUICK: Record<Mode, string[]> = {
  normal: [
    "Planeje a semana da Luna de segunda a quinta: 2 posts no Feed, 3 Stories e 1 TikTok por dia, com horário, tema, prompt para a Naiaflow e legenda.",
    "Me dê 5 ideias de fotos para amanhã, em cenários reais do Brasil.",
    "Escreva 3 opções de legenda e 5 hashtags para uma foto da Luna na praia ao pôr do sol.",
  ],
  adulto: ["Planeje os posts do canal +18 do Telegram de segunda a quinta (1 por dia), com prompt para a Naiaflow e texto do post."],
};

function load(): Record<Mode, Message[]> {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) ?? "{}");
    return { normal: Array.isArray(raw.normal) ? raw.normal : [], adulto: Array.isArray(raw.adulto) ? raw.adulto : [] };
  } catch {
    return { normal: [], adulto: [] };
  }
}

function save(chats: Record<Mode, Message[]>) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(chats));
  } catch {
    // navegador sem armazenamento: a conversa vale so ate fechar a pagina
  }
}

export function AssistantPage() {
  const [tab, setTab] = useState<"conversa" | "plano">(() => {
    try {
      return localStorage.getItem("luna-assistente-aba") === "plano" ? "plano" : "conversa";
    } catch {
      return "conversa";
    }
  });
  const [status, setStatus] = useState<Status | null>(null);
  const [mode, setMode] = useState<Mode>("normal");
  const [chats, setChats] = useState<Record<Mode, Message[]>>(load);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const messages = chats[mode];

  useEffect(() => {
    fetch("/api/studio?r=assistant")
      .then((r) => (r.ok ? r.json() : null))
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);
  // chaves: no Chrome novo scrollIntoView devolve uma Promise, e o React
  // trataria o retorno como funcao de limpeza
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, busy]);

  function update(next: Message[]) {
    setChats((c) => {
      const all = { ...c, [mode]: next };
      save(all);
      return all;
    });
  }

  async function send(content: string) {
    const body = content.trim();
    if (!body || busy) return;
    const next: Message[] = [...messages, { role: "user", content: body }];
    update(next);
    setText("");
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/studio?r=assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, messages: next }),
      });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
      update([...next, { role: "assistant", content: b.reply }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function copy(i: number) {
    try {
      await navigator.clipboard.writeText(messages[i].content);
      setCopied(i);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      setError("O navegador não deixou copiar. Selecione o texto da resposta.");
    }
  }

  return (
    <div className="st-assistant">
      <div className="st-page-head">
        <div>
          <h1>Assistente</h1>
          <p>Peça o plano da semana, prompts para a Naiaflow, legendas e ideias. A IA conhece a Luna, as regras e a sua agenda.</p>
        </div>
      </div>
      {status && !status.configured && (
        <p className="st-warn">Falta cadastrar a chave do OpenRouter (OPENROUTER_API_KEY) na Vercel e fazer um novo deploy.</p>
      )}
      <div className="st-tabs" role="tablist" aria-label="Assistente">
        {(
          [
            ["conversa", "Conversa"],
            ["plano", "Planejamento"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={tab === id ? "active" : ""}
            onClick={() => {
              setTab(id);
              try {
                localStorage.setItem("luna-assistente-aba", id);
              } catch {
                // sem armazenamento: so nao lembra a aba
              }
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "plano" && <PlanPanel />}
      {tab === "conversa" && (
      <section className="st-panel st-chat">
        <div className="st-chat-head">
          <div className="st-seg" role="tablist" aria-label="Conversa">
            <button type="button" className={mode === "normal" ? "active" : ""} onClick={() => setMode("normal")}>
              Redes (Instagram e TikTok)
            </button>
            <button type="button" className={mode === "adulto" ? "active" : ""} onClick={() => setMode("adulto")}>
              +18 Telegram
            </button>
          </div>
          {status?.configured && <small className="st-hint">IA: {mode === "adulto" ? status.adultModel : status.model}</small>}
          {messages.length > 0 && (
            <button type="button" className="st-btn st-btn-small" disabled={busy} onClick={() => window.confirm("Apagar esta conversa?") && update([])}>
              Limpar conversa
            </button>
          )}
        </div>
        {mode === "adulto" && <p className="st-hint">Só para o canal privado do Telegram. Nada daqui vai para Instagram ou TikTok.</p>}
        <div className="st-chat-list">
          {messages.length === 0 && (
            <div className="st-chat-quick">
              <span className="st-hint">Comece com um destes pedidos ou escreva o seu:</span>
              {QUICK[mode].map((q) => (
                <button key={q} type="button" className="st-btn st-btn-small" disabled={busy || !status?.configured} onClick={() => send(q)}>
                  {q}
                </button>
              ))}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`st-msg st-msg-${m.role}`}>
              <p>{m.content}</p>
              {m.role === "assistant" && (
                <button type="button" className="st-link" onClick={() => copy(i)}>
                  {copied === i ? "Copiado" : "Copiar"}
                </button>
              )}
            </div>
          ))}
          {busy && <div className="st-msg st-msg-assistant st-msg-wait">Pensando…</div>}
          <div ref={endRef} />
        </div>
        {error && <p className="st-error">{error}</p>}
        <form
          className="st-chat-input"
          onSubmit={(e) => {
            e.preventDefault();
            send(text);
          }}
        >
          <textarea
            className="st-input"
            rows={2}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(text);
              }
            }}
            placeholder="Escreva o que você precisa… (Enter envia, Shift+Enter pula linha)"
            aria-label="Mensagem para o Assistente"
          />
          <button type="submit" className="st-btn st-btn-primary" disabled={busy || !text.trim() || !status?.configured}>
            <Icon name="send" size={16} />
            Enviar
          </button>
        </form>
      </section>
      )}
    </div>
  );
}

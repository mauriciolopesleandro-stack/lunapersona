import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { PlanPanel } from "./plans";
import { parsePhotoBlocks, QuickPhoto } from "./quickPhoto";

// Assistente: chat do administrador com a IA que conhece a Luna (OpenRouter,
// pelo servidor). Uma conversa so, sempre com a IA sem restricao (pedido do
// administrador); o que vai para Instagram/TikTok continua seguindo as regras
// das redes. A conversa fica guardada neste navegador.

interface Message {
  role: "user" | "assistant";
  content: string;
}
interface Status {
  configured: boolean;
  model: string;
  adultModel: string;
}

const STORE_KEY = "luna-assistente-v2";
// Antes havia duas conversas (redes e +18); a primeira abertura aproveita a
// que tiver mensagens.
const OLD_STORE_KEY = "luna-assistente-v1";
const QUICK = [
  "Planeje a semana da Luna de segunda a quinta: 2 posts no Feed, 3 Stories e 1 TikTok por dia, com horário, tema, prompt para a Naiaflow e legenda.",
  "Planeje os posts do canal +18 do Telegram de segunda a quinta (1 por dia), com prompt para a Naiaflow e texto do post.",
  "Me dê 5 ideias de fotos para amanhã, em cenários reais do Brasil.",
];

function load(): Message[] {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) ?? "null");
    if (Array.isArray(saved)) return saved;
    const old = JSON.parse(localStorage.getItem(OLD_STORE_KEY) ?? "{}");
    if (Array.isArray(old.adulto) && old.adulto.length > 0) return old.adulto;
    return Array.isArray(old.normal) ? old.normal : [];
  } catch {
    return [];
  }
}

function save(messages: Message[]) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(messages));
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
  const [messages, setMessages] = useState<Message[]>(load);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

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
    setMessages(next);
    save(next);
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
        body: JSON.stringify({ messages: next }),
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
          {status?.configured && <small className="st-hint">IA sem restrição: {status.adultModel}</small>}
          {messages.length > 0 && (
            <button type="button" className="st-btn st-btn-small" disabled={busy} onClick={() => window.confirm("Apagar esta conversa?") && update([])}>
              Limpar conversa
            </button>
          )}
        </div>
        <p className="st-hint">Fala de tudo, sem restrição. Conteúdo explícito só vale para o canal +18 do Telegram; para Instagram e TikTok a IA segue as regras das redes.</p>
        <div className="st-chat-list">
          {messages.length === 0 && (
            <div className="st-chat-quick">
              <span className="st-hint">Comece com um destes pedidos ou escreva o seu:</span>
              {QUICK.map((q) => (
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
              {m.role === "assistant" &&
                parsePhotoBlocks(m.content).map((b, j) => <QuickPhoto key={`${i}-${j}`} block={b} />)}
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

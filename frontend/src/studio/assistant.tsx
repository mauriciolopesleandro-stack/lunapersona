import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { PlanPanel } from "./plans";
import { parsePhotoBlocks, QuickPhoto } from "./quickPhoto";
import { NETWORKS } from "./shared";
import { useStudio, type Post } from "./store";

// Assistente: chat do administrador com a IA que conhece a Luna (OpenRouter,
// pelo servidor). Uma conversa so, sempre com a IA sem restricao (pedido do
// administrador); o que vai para Instagram/TikTok continua seguindo as regras
// das redes. A conversa fica guardada neste navegador. Mora na barra lateral
// flutuante (dock.tsx), junto com a agenda e o planejamento.

interface Message {
  role: "user" | "assistant";
  content: string;
}
interface Status {
  configured: boolean;
  model: string;
  adultModel: string;
}
export type AssistantTab = "conversa" | "agenda" | "plano";

const STORE_KEY = "luna-assistente-v2";
// Antes havia duas conversas (redes e +18); a primeira abertura aproveita a
// que tiver mensagens.
const OLD_STORE_KEY = "luna-assistente-v1";
const QUICK = [
  "Planeje a semana da Luna de segunda a quinta: 2 posts no Feed, 3 Stories e 1 TikTok por dia, com horário, tema, prompt para a Naiaflow e legenda.",
  "Planeje os posts do canal +18 do Telegram de segunda a quinta (1 por dia), com prompt para a Naiaflow e texto do post.",
  "Me dê 5 ideias de fotos para amanhã, em cenários reais do Brasil.",
];
// sugestoes conforme a tela aberta
const CONTEXT: Record<string, string[]> = {
  creator: [
    "Escreva legendas e hashtags para os conteúdos aprovados que ainda não têm legenda.",
    "Me dê 5 ideias de Reels para esta semana, com gancho nos 2 primeiros segundos.",
  ],
  money: [
    "Escreva 5 chamadas novas para a mensagem diária do bot de vendas, com {nome}, sem nada explícito.",
    "Ideias de ensaio novo para segurar quem está no VIP e não deixar cancelar.",
  ],
  visao: ["O que está faltando nesta semana para a Luna não ficar sem post?", "Resuma o que está agendado para os próximos 3 dias."],
};

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

interface Props {
  context: string; // grupo da tela aberta (visao, creator, money...)
  tab: AssistantTab;
  onTab: (tab: AssistantTab) => void;
  draft: string | null; // texto que outra parte do estudio pediu para escrever
  onDraftUsed: () => void;
  onReply: () => void; // a IA respondeu (o dock marca se estiver minimizado)
  onOpenPost: (post: Post) => void;
  onNewPost: (date?: Date) => void;
  onFullCalendar: () => void;
}

export function AssistantPanel({ context, tab, onTab, draft, onDraftUsed, onReply, onOpenPost, onNewPost, onFullCalendar }: Props) {
  const [status, setStatus] = useState<Status | null>(null);
  const [messages, setMessages] = useState<Message[]>(load);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

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
  }, [messages.length, busy, tab]);
  useEffect(() => {
    if (draft === null) return;
    setText(draft);
    onDraftUsed();
    setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(draft.length, draft.length);
    }, 50);
  }, [draft, onDraftUsed]);

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
      onReply();
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

  const ideas = CONTEXT[context] ?? CONTEXT.visao;

  return (
    <div className="st-assistant">
      <div className="st-drawer-tabs" role="tablist" aria-label="Assistente">
        {(
          [
            ["conversa", "Conversa", "comment"],
            ["agenda", "Agenda", "calendar"],
            ["plano", "Planejamento", "star"],
          ] as const
        ).map(([id, label, icon]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => onTab(id)}>
            <Icon name={icon} size={15} />
            {label}
          </button>
        ))}
      </div>

      {status && !status.configured && <p className="st-warn">Falta cadastrar a chave do OpenRouter (OPENROUTER_API_KEY) na Vercel e fazer um novo deploy.</p>}

      {tab === "agenda" && <Agenda onOpenPost={onOpenPost} onNewPost={onNewPost} onFullCalendar={onFullCalendar} onAsk={(q) => { onTab("conversa"); send(q); }} />}
      {tab === "plano" && (
        <div className="st-drawer-scroll">
          <PlanPanel />
        </div>
      )}
      {tab === "conversa" && (
        <section className="st-chat">
          <div className="st-chat-list">
            {messages.length === 0 && (
              <div className="st-chat-quick">
                <span className="st-hint">Peça o plano da semana, prompts para a Naiaflow, legendas e ideias. A IA conhece a Luna, as regras e a sua agenda.</span>
                {[...ideas, ...QUICK].map((q) => (
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
                {m.role === "assistant" && parsePhotoBlocks(m.content).map((b, j) => <QuickPhoto key={`${i}-${j}`} block={b} />)}
              </div>
            ))}
            {busy && <div className="st-msg st-msg-assistant st-msg-wait">Pensando…</div>}
            <div ref={endRef} />
          </div>
          {error && <p className="st-error">{error}</p>}
          {messages.length > 0 && (
            <div className="st-chat-ideas">
              {ideas.map((q) => (
                <button key={q} type="button" disabled={busy || !status?.configured} onClick={() => send(q)} title={q}>
                  {q}
                </button>
              ))}
            </div>
          )}
          <form
            className="st-chat-input"
            onSubmit={(e) => {
              e.preventDefault();
              send(text);
            }}
          >
            <textarea
              ref={inputRef}
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
              placeholder="Escreva o que você precisa… (Enter envia)"
              aria-label="Mensagem para o Assistente"
            />
            <button type="submit" className="st-btn st-btn-primary" disabled={busy || !text.trim() || !status?.configured} aria-label="Enviar">
              <Icon name="send" size={16} />
            </button>
          </form>
          <div className="st-chat-foot">
            {status?.configured && <small className="st-hint">IA sem restrição: {status.adultModel}. Explícito só vale para o canal +18.</small>}
            {messages.length > 0 && (
              <button type="button" className="st-link" disabled={busy} onClick={() => window.confirm("Apagar esta conversa?") && update([])}>
                Limpar conversa
              </button>
            )}
          </div>
        </section>
      )}
    </div>
  );
}

// --- Agenda: os proximos 14 dias, por dia ------------------------------------------------

function Agenda({
  onOpenPost,
  onNewPost,
  onFullCalendar,
  onAsk,
}: {
  onOpenPost: (post: Post) => void;
  onNewPost: (date?: Date) => void;
  onFullCalendar: () => void;
  onAsk: (q: string) => void;
}) {
  const { posts, state } = useStudio();
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Array.from({ length: 14 }, (_, i) => new Date(today.getTime() + i * 86400_000));
  const active = posts.filter((p) => p.scheduledAt && p.status !== "cancelado");
  const onDay = (d: Date) =>
    active
      .filter((p) => new Date(p.scheduledAt!).toDateString() === d.toDateString())
      .sort((a, b) => (a.scheduledAt! < b.scheduledAt! ? -1 : 1));
  const empty = days.slice(0, 7).filter((d) => onDay(d).length === 0);

  if (state !== "ready") return <div className="st-empty st-empty-small">Conecte a pasta do estúdio para ver a agenda.</div>;

  return (
    <div className="st-drawer-scroll st-agenda">
      <div className="st-agenda-head">
        <button type="button" className="st-btn st-btn-small" onClick={() => onNewPost()}>
          <Icon name="plus" size={14} />
          Nova publicação
        </button>
        <button type="button" className="st-link" onClick={onFullCalendar}>
          Calendário completo →
        </button>
      </div>
      {empty.length > 0 && (
        <button
          type="button"
          className="st-agenda-gap"
          onClick={() =>
            onAsk(
              `Estes dias da próxima semana estão sem nenhum post: ${empty
                .map((d) => d.toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "2-digit" }))
                .join(", ")}. Sugira o que postar em cada um (rede, horário, tema, prompt para a Naiaflow e legenda).`
            )
          }
        >
          <Icon name="star" size={15} />
          {empty.length} dia{empty.length > 1 ? "s" : ""} sem post nesta semana · pedir sugestões
        </button>
      )}
      {days.map((d) => {
        const list = onDay(d);
        const isToday = d.getTime() === today.getTime();
        return (
          <section key={d.toISOString()} className={list.length ? "st-agenda-day" : "st-agenda-day empty"}>
            <header>
              <b>{isToday ? "Hoje" : d.toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "")}</b>
              <span>{d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })}</span>
              <button type="button" className="st-icon-btn st-agenda-add" aria-label="Nova publicação neste dia" onClick={() => onNewPost(new Date(d.getTime() + 12 * 3600_000))}>
                <Icon name="plus" size={14} />
              </button>
            </header>
            {list.map((p) => (
              <button key={p.id} type="button" className="st-agenda-post" onClick={() => onOpenPost(p)}>
                <time>{new Date(p.scheduledAt!).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}</time>
                <Icon name={NETWORKS[p.network].icon} size={14} />
                <span>{p.caption || p.formats.join(", ") || "Sem legenda"}</span>
                <i className={`st-agenda-status st-agenda-${p.status}`} title={p.status} />
              </button>
            ))}
          </section>
        );
      })}
    </div>
  );
}

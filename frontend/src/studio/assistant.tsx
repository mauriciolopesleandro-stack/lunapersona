import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { PlanPanel } from "./plans";
import { parsePhotoBlocks, QuickPhoto } from "./quickPhoto";
import { DANCE_LINK, DanceCard } from "./dance";
import { MetasPanel } from "./metas";
import { NETWORKS } from "./shared";
import { useStudio, type Post } from "./store";

// Assistente: chat do administrador com a IA que conhece a Luna (OpenRouter,
// pelo servidor). Uma conversa so, sempre com a IA sem restricao (pedido do
// administrador); o que vai para Instagram/TikTok continua seguindo as regras
// das redes. A conversa fica guardada neste navegador. Mora na barra lateral
// flutuante (dock.tsx), junto com a agenda e o planejamento.

interface Message {
  role: "user" | "assistant";
  content: string; // o que vai para a IA (com a leitura das referencias)
  display?: string; // o que o administrador escreveu
  refs?: string[]; // miniaturas das fotos/videos mandados
  reading?: string; // leitura das referencias (abre e fecha na conversa)
  dance?: { link?: string }; // video ou link de dancinha: mostra o "Fazer dancinha"
}
// foto ou video anexado, ja reduzido no navegador
interface Attachment {
  kind: "foto" | "video";
  thumb: string;
  images: string[];
  file?: File; // o video inteiro (para a dancinha), so enquanto a pagina esta aberta
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
// pedido de revisao junto com a foto/Reels da Luna ("o que esta errado?")
const REVIEW_ASK = /errad|defeito|problema|revis|analis|corrig|ruim|ficou bo|pode postar|t[aá] bo/i;
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

// sala +18 (aba Monetizacao): conversa separada, so para o canal pago
const ROOM18_KEY = "luna-assistente-18";
const QUICK18 = [
  "Monte 5 ideias de fotos +18 para o canal VIP desta semana, com prompt detalhado de cena real e o caminho mais barato na Naiaflow (retoque grátis, Ação ou Criar).",
  "Como transformar uma foto sensual já aprovada do Feed numa versão +18 usando o retoque grátis? Escreva o pedido do retoque.",
  "Escreva 3 textos curtos para posts do canal VIP que façam o assinante comentar e não cancelar.",
];

function load(room: string): Message[] {
  if (room === "18") {
    try {
      const saved = JSON.parse(localStorage.getItem(ROOM18_KEY) ?? "null");
      return Array.isArray(saved) ? saved : [];
    } catch {
      return [];
    }
  }
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

function save(messages: Message[], room: string) {
  try {
    localStorage.setItem(room === "18" ? ROOM18_KEY : STORE_KEY, JSON.stringify(messages));
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
  room?: "geral" | "18"; // "18": sala do canal pago (so a conversa, historico proprio)
}

export function AssistantPanel({ context, tab, onTab, draft, onDraftUsed, onReply, onOpenPost, onNewPost, onFullCalendar, room = "geral" }: Props) {
  const [status, setStatus] = useState<Status | null>(null);
  const [messages, setMessages] = useState<Message[]>(() => load(room));
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [stage, setStage] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // arquivos de video das dancinhas pedidas nesta visita (indice da mensagem)
  const danceFiles = useRef(new Map<number, File>());
  // sala +18: foto mandada (indice da mensagem) vira referencia (IMG1) do Criar
  const refPhotos = useRef(new Map<number, string>());

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
    save(next, room);
  }

  async function attach(files: File[]) {
    setError(null);
    try {
      const list = [...attachments];
      for (const f of files) {
        if (f.type.startsWith("video/")) {
          if (list.some((a) => a.kind === "video")) throw new Error("Mande um vídeo por vez.");
          list.push(await videoRef(f));
        } else if (f.type.startsWith("image/")) {
          if (list.filter((a) => a.kind === "foto").length >= 4) throw new Error("Até 4 fotos por mensagem.");
          list.push(await photoRef(f));
        }
      }
      if (list.some((a) => a.kind === "video") && list.length > 1) throw new Error("Vídeo vai sozinho na mensagem (sem fotos junto).");
      setAttachments(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function send(content: string) {
    const typed = content.trim();
    const link = typed.match(/https?:\/\/\S+/)?.[0];
    if ((!typed && !attachments.length) || busy) return;
    setBusy(true);
    setError(null);
    // 1) leitura das referencias (fotos, video ou link) pela IA com visao;
    // "o que esta errado?" numa foto/Reels da Luna vira revisao (defeitos)
    const review = attachments.length > 0 && REVIEW_ASK.test(typed);
    const reads: { label: string; text: string }[] = [];
    try {
      for (const a of attachments) {
        setStage(review ? "Revisando…" : a.kind === "video" ? "Assistindo o vídeo…" : "Olhando a foto…");
        try {
          const out = await post<{ analysis: string }>({ action: "analyze", kind: a.kind, images: a.images, ...(review ? { purpose: "review" } : {}) });
          reads.push({ label: a.kind, text: out.analysis });
        } catch (e) {
          // sala +18: a IA de visao pode recusar foto explicita; a foto segue como referencia (IMG1)
          if (room !== "18") throw e;
          reads.push({ label: a.kind, text: "(a leitura automática da foto não saiu; ela vai como referência IMG1 do mesmo jeito)" });
        }
      }
      if (link) {
        setStage(/instagram\.com/i.test(link) ? "Lendo a página do Instagram…" : "Lendo o link…");
        try {
          const out = await post<{ title: string; analysis: string }>({ action: "link", url: link });
          reads.push({ label: out.title, text: out.analysis });
        } catch (e) {
          // link de dancinha (TikTok, Reels...) costuma bloquear a leitura: segue
          // sem ela, o "Fazer dancinha" usa o link direto
          if (!DANCE_LINK.test(link)) throw e;
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
      setStage(null);
      return;
    }
    const profile = reads.some((r) => r.label.startsWith("perfil @"));
    const ask = typed || (profile ? "Analise essa página e crie conteúdos para a Luna nesse estilo." : "Recria essa ideia com a Luna.");
    const reading = reads.map((r) => `[Referência: ${r.label}]\n${r.text}`).join("\n\n");
    const recreate = review
      ? "Esta é uma foto/Reels da própria Luna gerado pela Naiaflow. Com a revisão acima, diga em tópicos curtos o que está errado (ou se está pronta para postar) e como acertar: se der para corrigir editando, escreva o pedido curto para o ✏️ Pedir correção do Telegram (Editar pelo texto da Naiaflow); se for melhor gerar de novo, escreva o prompt corrigido com o bloco PROMPT/LEGENDA/HASHTAGS/DESTINO, seguindo as regras de prompt (roupa completa, enquadramento, mãos simples)."
      : profile
      ?"Use a leitura da página para criar 5 conteúdos para a Luna no mesmo estilo (inspire-se, não copie a pessoa, o rosto nem a marca). Termine com um bloco PROMPT/LEGENDA/HASHTAGS/DESTINO para cada um."
      : room === "18"
      ? "A foto que mandei vai como referência IMG1 no Criar da Naiaflow: a luna copia a pose, o ângulo e o enquadramento dela. Escreva o PROMPT no padrão da Naia para referência: NÃO descreva a pose de novo; escreva com bastante detalhe o que a foto não entrega — roupa (ou nua), cenário, expressão, luz, textura da pele e estilo da foto. Termine com o bloco PROMPT/LEGENDA/HASHTAGS/DESTINO: +18 Telegram."
      : "Recrie a ideia da referência com a Luna (inspire-se: mesmo clima, cenário, roupa, pose e ângulo; nunca a pessoa da referência). Termine com o bloco PROMPT/LEGENDA/HASHTAGS/DESTINO (um por foto). Se for vídeo, diga também como fazer o Reels (foto da Luna com zoom no PC ou o Copiar movimento da Naiaflow).";
    const message: Message = reads.length
      ? { role: "user", content: `${ask}\n\n${reading}\n\n${recreate}`.slice(0, 7900), display: ask, refs: attachments.map((a) => a.thumb), reading }
      : { role: "user", content: typed };
    // dancinha: o video enviado (o arquivo fica so na memoria) ou o link de rede social
    const danceFile = room !== "18" ? attachments.find((a) => a.kind === "video")?.file : undefined;
    const danceLink = room !== "18" ? typed.match(DANCE_LINK)?.[0] : undefined;
    if (danceFile || danceLink) message.dance = danceLink && !danceFile ? { link: danceLink } : {};
    if (danceFile) danceFiles.current.set(messages.length, danceFile);
    const refPhoto = room === "18" ? attachments.find((a) => a.kind === "foto")?.images[0] : undefined;
    if (refPhoto) refPhotos.current.set(messages.length, refPhoto);
    const next: Message[] = [...messages, message];
    update(next);
    setText("");
    setAttachments([]);
    setStage(null);
    try {
      const r = await fetch("/api/studio?r=assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: next.map((m) => ({ role: m.role, content: m.content })), ...(room === "18" ? { room: "18" } : {}) }),
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

  const ideas = room === "18" ? QUICK18.slice(0, 2) : (CONTEXT[context] ?? CONTEXT.visao);
  const quick = room === "18" ? QUICK18 : [...ideas, ...QUICK];

  return (
    <div className={room === "18" ? "st-assistant st-assistant-18" : "st-assistant"}>
      {room !== "18" && <div className="st-drawer-tabs" role="tablist" aria-label="Assistente">
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
      </div>}

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
                <span className="st-hint">
                  {room === "18"
                    ? "Sala +18: só para o canal VIP do Telegram. Peça ideias, prompts detalhados e textos; mande uma foto de referência (📎) e a IA diz o caminho mais barato na Naiaflow (retoque grátis, Ação ou Criar)."
                    : "Peça o plano da semana, prompts para a Naiaflow, legendas e ideias. Mande uma foto ou vídeo de referência (📎) ou cole o link de um perfil do Instagram: a IA faz a leitura e recria a ideia com a Luna."}
                </span>
                {quick.map((q) => (
                  <button key={q} type="button" className="st-btn st-btn-small" disabled={busy || !status?.configured} onClick={() => send(q)}>
                    {q}
                  </button>
                ))}
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`st-msg st-msg-${m.role}`}>
                {m.refs && m.refs.length > 0 && (
                  <div className="st-msg-refs">
                    {m.refs.map((src, j) => (
                      <img key={j} src={src} alt="Referência" />
                    ))}
                  </div>
                )}
                <p>{m.display ?? m.content}</p>
                {m.reading && (
                  <details className="st-msg-reading">
                    <summary>Leitura da referência</summary>
                    <p>{m.reading}</p>
                  </details>
                )}
                {m.role === "user" && m.dance && <DanceCard link={m.dance.link} file={danceFiles.current.get(i)} />}
                {m.role === "assistant" && (
                  <button type="button" className="st-link" onClick={() => copy(i)}>
                    {copied === i ? "Copiado" : "Copiar"}
                  </button>
                )}
                {m.role === "assistant" &&
                  parsePhotoBlocks(m.content).map((b, j) => (
                    <QuickPhoto key={`${i}-${j}`} block={b} reference={room === "18" ? refPhotos.current.get(i - 1) : undefined} />
                  ))}
              </div>
            ))}
            {busy && <div className="st-msg st-msg-assistant st-msg-wait">{stage ?? "Pensando…"}</div>}
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
          {attachments.length > 0 && (
            <div className="st-chat-attach">
              {attachments.map((a, j) => (
                <span key={j}>
                  <img src={a.thumb} alt="" />
                  {a.kind === "video" && <i>vídeo</i>}
                  <button type="button" aria-label="Tirar" onClick={() => setAttachments(attachments.filter((_, k) => k !== j))}>
                    <Icon name="close" size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <input
            ref={fileRef}
            type="file"
            hidden
            multiple
            accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm"
            onChange={(e) => {
              const files = [...(e.target.files ?? [])];
              e.target.value = "";
              attach(files);
            }}
          />
          <form
            className="st-chat-input"
            onSubmit={(e) => {
              e.preventDefault();
              send(text);
            }}
          >
            <button type="button" className="st-btn st-chat-clip" disabled={busy} onClick={() => fileRef.current?.click()} aria-label="Anexar foto ou vídeo" title="Anexar foto ou vídeo (ou cole uma imagem)">
              <Icon name="image" size={16} />
            </button>
            <textarea
              ref={inputRef}
              className="st-input"
              rows={2}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onPaste={(e) => {
                const files = [...e.clipboardData.files].filter((f) => f.type.startsWith("image/"));
                if (files.length) {
                  e.preventDefault();
                  attach(files);
                }
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(text);
                }
              }}
              placeholder="Escreva, mande uma foto/vídeo ou cole um link do Instagram…"
              aria-label="Mensagem para o Assistente"
            />
            <button type="submit" className="st-btn st-btn-primary" disabled={busy || (!text.trim() && !attachments.length) || !status?.configured} aria-label="Enviar">
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

async function post<T>(body: object): Promise<T> {
  const r = await fetch("/api/studio?r=assistant", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
  return b as T;
}

// imagem/quadro reduzido em JPEG (a IA nao precisa da foto inteira)
function shrink(src: CanvasImageSource, w: number, h: number, max: number, quality: number): string {
  const scale = Math.min(1, max / Math.max(w, h));
  const c = Object.assign(document.createElement("canvas"), { width: Math.round(w * scale), height: Math.round(h * scale) });
  c.getContext("2d")!.drawImage(src, 0, 0, c.width, c.height);
  return c.toDataURL("image/jpeg", quality);
}

async function photoRef(file: File): Promise<Attachment> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error(`Não consegui abrir “${file.name}”. Mande em JPG ou PNG.`);
  }
  return { kind: "foto", thumb: shrink(bitmap, bitmap.width, bitmap.height, 160, 0.7), images: [shrink(bitmap, bitmap.width, bitmap.height, 1024, 0.82)] };
}

// 4 quadros do video (10%, 35%, 60% e 85%) para a IA entender o movimento
async function videoRef(file: File): Promise<Attachment> {
  const url = URL.createObjectURL(file);
  try {
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    video.src = url;
    await new Promise<void>((ok, fail) => {
      video.onloadeddata = () => ok();
      video.onerror = () => fail(new Error(`Não consegui abrir o vídeo “${file.name}”. Mande em MP4.`));
    });
    const duration = video.duration || 1;
    const frames: string[] = [];
    for (const at of [0.1, 0.35, 0.6, 0.85]) {
      video.currentTime = duration * at;
      await new Promise<void>((ok) => (video.onseeked = () => ok()));
      frames.push(shrink(video, video.videoWidth, video.videoHeight, 768, 0.8));
    }
    return { kind: "video", thumb: shrink(video, video.videoWidth, video.videoHeight, 160, 0.7), images: frames, file };
  } finally {
    URL.revokeObjectURL(url);
  }
}

// --- Sala +18 (aba Monetizacao -> Chat +18) --------------------------------------------------

export function Chat18Page() {
  return (
    <section className="st-panel st-chat18">
      <AssistantPanel
        room="18"
        context="money"
        tab="conversa"
        onTab={() => undefined}
        draft={null}
        onDraftUsed={() => undefined}
        onReply={() => undefined}
        onOpenPost={() => undefined}
        onNewPost={() => undefined}
        onFullCalendar={() => undefined}
      />
    </section>
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
      <MetasPanel days={14} />
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

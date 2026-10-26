import { useEffect, useMemo, useState } from "react";
import { aspectRatio, ASPECTS, flagProblem, translateFlags, type Aspect, type IgAudioFlag, type PostFlags } from "../../api/_flags";
import { Icon } from "./Icon";
import { Modal, NETWORKS, Thumb } from "./shared";
import { useStudio, type Item, type Network, type Post } from "./store";

// Publicar: assistente em quatro etapas (Criar, Configurar, Revisar, Publicar).
// Cada combinacao "midia (ou carrossel) x formato" escolhida na grade vira UMA
// publicacao propria (rede, formato, midias, legenda, recorte e horario): nada
// vai para uma rede que nao foi marcada. Todas as publicacoes da mesma sessao
// ficam no mesmo grupo (aprovar todas de uma vez) e as opcoes ("flags") sao
// traduzidas por rede (api/_flags.ts). O envio usa a fila que ja existe.

type Slot = "ig_feed" | "ig_stories" | "ig_reels" | "tt" | "tg";
type Mode = "rascunho" | "aprovacao" | "telegram" | "publicar";

const SLOTS: { id: Slot; network: Network; label: string; hint: string }[] = [
  { id: "ig_feed", network: "instagram", label: "Feed", hint: "foto, vídeo ou carrossel" },
  { id: "ig_stories", network: "instagram", label: "Stories", hint: "1 mídia, some em 24 h" },
  { id: "ig_reels", network: "instagram", label: "Reels", hint: "1 vídeo" },
  { id: "tt", network: "tiktok", label: "TikTok", hint: "1 vídeo ou fotos" },
  { id: "tg", network: "telegram", label: "Telegram", hint: "lembrete no canal" },
];
const PLATFORMS: Network[] = ["instagram", "tiktok", "telegram"];
const CAROUSEL = "__carrossel__";

interface Pub {
  key: string; // slot:unidade (caminho da midia ou carrossel)
  slot: Slot;
  unit: string;
  aspect: Aspect | "original";
  ownCaption: boolean;
  caption: string;
  igAudio?: IgAudioFlag; // musica da biblioteca do Instagram (so Reels)
}

interface Platform {
  on: boolean;
  auto: boolean;
  caption: string;
  hashtags: string[];
  location: string;
}

interface Song {
  id: string;
  name: string;
  artist: string;
  artwork: string;
  previewUrl: string | null;
}

interface Result {
  key: string;
  ok: boolean;
  text: string;
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function localDate(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function slotOf(id: Slot) {
  return SLOTS.find((s) => s.id === id)!;
}

// Formato gravado na publicacao (o que a fila entende).
function formatOf(slot: Slot, media: Item[]): string {
  if (slot === "ig_feed") return "Feed";
  if (slot === "ig_stories") return "Stories";
  if (slot === "ig_reels") return "Reels";
  if (slot === "tt") return media.some((m) => m.kind === "video") ? "Vídeo" : "Foto";
  return "Canal";
}

// A combinacao e possivel? null = sim; senao, o motivo (mostrado na grade).
function incompatible(slot: Slot, media: Item[]): string | null {
  const videos = media.filter((m) => m.kind === "video").length;
  if (slot === "ig_feed") return media.length > 20 ? "o Feed aceita até 20 mídias" : null;
  if (slot === "ig_stories") return media.length !== 1 ? "Stories leva 1 mídia por vez (marque cada uma)" : null;
  if (slot === "ig_reels") return media.length !== 1 || videos !== 1 ? "Reels precisa de 1 vídeo" : null;
  if (slot === "tt") {
    if (videos && media.length > 1) return "o TikTok não junta vídeo com outras mídias";
    return media.length > 35 ? "o TikTok aceita até 35 fotos" : null;
  }
  return media.length > 10 ? "o Telegram aceita até 10 mídias" : null;
}

export interface PostarStart {
  key: number;
  date?: Date;
  media?: string[];
}

export function PostarPage({ start, onDone }: { start: PostarStart; onDone: () => void }) {
  const { items, saveCloudPost, postAction, approveAutomatic, sendToTelegram, instagram, tiktok, state, source } = useStudio();
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [media, setMedia] = useState<string[]>(start.media ?? []);
  const [slide, setSlide] = useState(0);
  const [aspect, setAspect] = useState<Aspect | "original">("original");
  const [cover, setCover] = useState(0);
  const [picking, setPicking] = useState(false);
  const [pubs, setPubs] = useState<Pub[]>([]);
  const [platforms, setPlatforms] = useState<Record<Network, Platform>>({
    instagram: { on: true, auto: true, caption: "", hashtags: [], location: "" },
    tiktok: { on: true, auto: true, caption: "", hashtags: [], location: "" },
    telegram: { on: false, auto: false, caption: "", hashtags: [], location: "" },
  });
  const [tagInputs, setTagInputs] = useState<Record<Network, string>>({ instagram: "", tiktok: "", telegram: "" });
  const [music, setMusic] = useState("");
  const [songs, setSongs] = useState<Song[]>([]);
  const [playing, setPlaying] = useState<HTMLAudioElement | null>(null);
  const [aiGenerated, setAiGenerated] = useState(false);
  const [commentsOff, setCommentsOff] = useState(false);
  const [when, setWhen] = useState<"agora" | "depois">(start.date ? "depois" : "agora");
  const startDate = start.date ?? new Date(Date.now() + 3600_000);
  const [date, setDate] = useState(localDate(startDate));
  const [time, setTime] = useState(`${pad(startDate.getHours())}:${pad(startDate.getMinutes())}`);
  const [previewSlot, setPreviewSlot] = useState<Slot>("ig_feed");
  const [previewKey, setPreviewKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [results, setResults] = useState<Result[]>([]);

  useEffect(() => {
    fetch("/api/studio?r=music")
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => b?.songs && setSongs(b.songs))
      .catch(() => undefined);
    return () => playing?.pause();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const byPath = useMemo(() => new Map(items.map((i) => [i.path, i])), [items]);
  const chosen = media.map((p) => byPath.get(p)).filter((i): i is Item => Boolean(i));
  const current = chosen[Math.min(slide, chosen.length - 1)];
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  // midias de uma publicacao (carrossel = todas, na ordem escolhida)
  const unitMedia = (unit: string): Item[] => (unit === CAROUSEL ? chosen : chosen.filter((m) => m.path === unit));
  const units: { id: string; label: string; media: Item[] }[] = [
    ...chosen.map((m, i) => ({ id: m.path, label: `${m.kind === "video" ? "Vídeo" : "Imagem"} ${i + 1}`, media: [m] })),
    ...(chosen.length > 1 ? [{ id: CAROUSEL, label: `Carrossel (${chosen.length} mídias, na ordem)`, media: chosen }] : []),
  ];
  // so publicacoes cujas midias ainda existem e cuja rede esta ligada
  const active = pubs.filter((p) => unitMedia(p.unit).length && platforms[slotOf(p.slot).network].on);

  const pubFlags = (p: Pub): PostFlags => {
    const plat = platforms[slotOf(p.slot).network];
    return {
      ...(aiGenerated ? { aiGenerated: true } : {}),
      ...(music.trim() ? { music: music.trim() } : {}),
      ...(commentsOff ? { commentsOff: true } : {}),
      ...(p.unit === CAROUSEL && cover > 0 && cover < chosen.length ? { coverIndex: cover } : {}),
      ...(p.aspect !== "original" ? { aspect: p.aspect } : {}),
      ...(plat.location.trim() ? { location: plat.location.trim() } : {}),
      ...(p.slot === "ig_reels" && p.igAudio ? { igAudio: p.igAudio } : {}),
    };
  };
  const pubCaption = (p: Pub) => (p.ownCaption ? p.caption : platforms[slotOf(p.slot).network].caption);
  const pubMode = (p: Pub): "api" | "manual" => {
    const n = slotOf(p.slot).network;
    return n !== "telegram" && platforms[n].auto ? "api" : "manual";
  };
  const connected = (n: Network) => (n === "instagram" ? Boolean(instagram?.connected) : n === "tiktok" ? Boolean(tiktok?.connected) : true);

  // Pendencias que impedem publicar (por publicacao) e avisos (nao impedem).
  const pubProblems = (p: Pub): string[] => {
    const s = slotOf(p.slot);
    const net = NETWORKS[s.network];
    const m = unitMedia(p.unit);
    const out: string[] = [];
    const why = incompatible(p.slot, m);
    if (why) out.push(why);
    const caption = pubCaption(p);
    if (caption.length > net.captionMax) out.push(`legenda passou do limite do ${net.label} (${caption.length}/${net.captionMax})`);
    const tags = platforms[s.network].hashtags.length;
    if (s.network === "instagram" && tags > 30) out.push("o Instagram aceita até 30 hashtags");
    for (const it of m) {
      if (it.rating === "adulto" && s.network !== "telegram") out.push(`“${it.title}” é conteúdo adulto (só Telegram)`);
      else if (it.destinations?.length && !it.destinations.includes(s.network)) out.push(`“${it.title}” não está liberado para ${net.label}`);
    }
    const issue = flagProblem(s.network, [formatOf(p.slot, m)], pubFlags(p));
    if (issue) out.push(issue);
    if (pubMode(p) === "api" && !connected(s.network)) out.push(`conta do ${net.label} desconectada: conecte em Contas conectadas ou desligue o automático`);
    return out;
  };
  const scheduleProblem =
    when === "depois" && (!date || !time || new Date(`${date}T${time}`).getTime() < Date.now() - 60_000) ? "o horário agendado já passou ou está vazio" : null;
  const blockingCount = active.reduce((n, p) => n + pubProblems(p).length, 0) + (scheduleProblem ? 1 : 0);
  const warnings: string[] = [];
  if (music.trim() && active.some((p) => pubMode(p) === "api")) warnings.push("Música não vai automática: as APIs não colocam música (no TikTok você põe no rascunho; no Instagram, só publicando pelo app).");
  if (active.some((p) => slotOf(p.slot).network === "tiktok" && pubMode(p) === "api")) warnings.push("TikTok: vai como rascunho para o app (publicar direto exige a auditoria do TikTok). Você abre, põe a música e publica.");
  if (active.some((p) => pubMode(p) === "manual")) warnings.push("Publicações manuais: no horário o estúdio avisa e você publica pelo app e confirma em Publicações.");

  if (source !== "banco") return <div className="st-empty">Para publicar, ligue o banco de dados (Configurações).</div>;
  if (state !== "ready") return <div className="st-empty">Abra a pasta do estúdio (barra lateral) para escolher as mídias.</div>;

  function togglePub(slot: Slot, unit: string) {
    const key = `${slot}:${unit}`;
    setPubs((list) => {
      if (list.some((p) => p.key === key)) return list.filter((p) => p.key !== key);
      // padrao de recorte: o do passo Criar; Feed nao aceita 9:16
      const a = slot === "ig_feed" && aspect === "9:16" ? "4:5" : aspect;
      return [...list, { key, slot, unit, aspect: a, ownCaption: false, caption: "" }];
    });
  }

  function patchPub(key: string, patch: Partial<Pub>) {
    setPubs((list) => list.map((p) => (p.key === key ? { ...p, ...patch } : p)));
  }

  function setPlatform(n: Network, patch: Partial<Platform>) {
    setPlatforms((p) => ({ ...p, [n]: { ...p[n], ...patch } }));
  }

  function addTags(n: Network) {
    const tags = tagInputs[n]
      .split(/[\s,]+/)
      .map((t) => t.replace(/^#+/, "").trim())
      .filter(Boolean)
      .map((t) => `#${t}`);
    if (tags.length) setPlatform(n, { hashtags: [...platforms[n].hashtags, ...tags.filter((t) => !platforms[n].hashtags.includes(t))] });
    setTagInputs((t) => ({ ...t, [n]: "" }));
  }

  function move(i: number, by: -1 | 1) {
    const j = i + by;
    if (j < 0 || j >= media.length) return;
    setMedia((m) => {
      const next = [...m];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
    if (cover === i) setCover(j);
    else if (cover === j) setCover(i);
    setSlide(j);
  }

  function removeAt(i: number) {
    const path = media[i];
    setMedia((m) => m.filter((_, k) => k !== i));
    setCover((c) => (c === i ? 0 : c > i ? c - 1 : c));
    setSlide(0);
    // a publicacao so daquela midia sai junto (as outras ficam como estao)
    setPubs((list) => list.filter((p) => p.unit !== path));
  }

  function playSong(song: Song) {
    playing?.pause();
    if (!song.previewUrl) return;
    const a = new Audio(song.previewUrl);
    a.play().catch(() => undefined);
    setPlaying(a);
  }

  async function finish(mode: Mode) {
    setMessage(null);
    setResults([]);
    if (!active.length) return setMessage({ ok: false, text: "Marque pelo menos uma publicação na grade (passo 2)." });
    if (mode !== "rascunho" && blockingCount) return setMessage({ ok: false, text: "Há pendências: veja os alertas no passo 3." });
    const at = when === "agora" ? new Date() : new Date(`${date}T${time}`);
    if (mode === "publicar") {
      const lines = active.map((p) => `• ${NETWORKS[slotOf(p.slot).network].label} ${slotOf(p.slot).label}: ${unitMedia(p.unit).length} mídia(s)`).join("\n");
      const hora = when === "agora" ? "agora" : at.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
      if (!window.confirm(`Publicar ${hora}?\n\n${lines}`)) return;
    }
    const groupId = active.length > 1 ? crypto.randomUUID() : null;
    setBusy(true);
    const out: Result[] = [];
    // cada publicacao segue sozinha: erro numa nao impede as outras
    for (const p of active) {
      const s = slotOf(p.slot);
      const m = unitMedia(p.unit);
      const label = `${NETWORKS[s.network].label} ${s.label}`;
      try {
        setMessage({ ok: true, text: `${label}: salvando...` });
        const { post } = await saveCloudPost({
          network: s.network,
          formats: [formatOf(p.slot, m)],
          media: m.map((x) => x.path),
          caption: pubCaption(p),
          hashtags: platforms[s.network].hashtags,
          scheduledAt: at.toISOString(),
          publishMode: pubMode(p),
          flags: pubFlags(p),
          groupId,
        });
        if (mode !== "rascunho") await postAction("submit", post.id);
        if (mode === "telegram" && post.publishMode === "api") {
          await sendToTelegram({ ...post, status: "aguardando_aprovacao" }, (text) => setMessage({ ok: true, text: `${label}: ${text}` }));
        }
        if (mode === "publicar") {
          const ready: Post = { ...post, status: "aguardando_aprovacao" };
          if (ready.publishMode === "api") await approveAutomatic(ready, (text) => setMessage({ ok: true, text: `${label}: ${text}` }));
          else await postAction("approve", post.id, { version: post.version });
        }
        out.push({
          key: p.key,
          ok: true,
          text:
            mode === "publicar"
              ? pubMode(p) === "api"
                ? "na fila"
                : "agendada (ação manual no horário)"
              : mode === "telegram"
              ? pubMode(p) === "api"
                ? "no Telegram para você aprovar"
                : "aguardando aprovação (manual: aprove no estúdio)"
              : mode === "aprovacao"
              ? "aguardando aprovação"
              : "rascunho salvo",
        });
      } catch (e) {
        out.push({ key: p.key, ok: false, text: e instanceof Error ? e.message : String(e) });
      }
      setResults([...out]);
    }
    setBusy(false);
    const failed = out.filter((r) => !r.ok).length;
    setMessage(
      failed
        ? { ok: false, text: `${out.length - failed} de ${out.length} feitas; ${failed} com erro (veja abaixo). As que deram certo seguem normalmente.` }
        : {
            ok: true,
            text:
              mode === "publicar"
                ? `${out.length} publicação(ões) aprovada(s). ${when === "agora" ? "O envio sai no próximo minuto" : "No horário o estúdio envia"}; acompanhe em Publicações.`
                : mode === "telegram"
                  ? `${out.length} publicação(ões) mandada(s) para você aprovar no Telegram.`
                  : mode === "aprovacao"
                  ? `${out.length} publicação(ões) enviada(s) para aprovação.`
                  : `${out.length} rascunho(s) salvo(s).`,
          }
    );
    if (!failed) setTimeout(onDone, 2200);
  }

  const steps = [
    ["Criar", "Escolher e editar mídia"],
    ["Configurar", "Redes e legendas"],
    ["Revisar", "Pré-visualização"],
    ["Publicar", "Enviar conteúdo"],
  ] as const;
  const canGo = (s: number) => s === 1 || (s === 2 && chosen.length > 0) || (s >= 3 && chosen.length > 0 && active.length > 0);
  const primaryLabel = blockingCount ? "Corrigir pendências" : when === "agora" ? "Publicar agora" : "Agendar publicação";

  // a previa abre na primeira rede marcada (ex.: so Reels -> nao mostra o Feed vazio)
  const shownSlot = active.some((p) => p.slot === previewSlot) ? previewSlot : active.find((p) => p.slot !== "tg")?.slot ?? previewSlot;
  const previewPubs = active.filter((p) => p.slot === shownSlot);
  const shownPub = previewPubs.find((p) => p.key === previewKey) ?? previewPubs[0];

  return (
    <div className="st-postar">
      <ol className="st-steps-bar st-postar-steps" aria-label="Etapas">
        {steps.map(([label, sub], i) => (
          <li key={label} className={step === i + 1 ? "on" : step > i + 1 ? "done" : ""}>
            <button type="button" onClick={() => canGo(i + 1) && setStep((i + 1) as 1 | 2 | 3 | 4)} disabled={!canGo(i + 1)}>
              <span>{i + 1}</span>
              <b>{label}</b>
              <small>{sub}</small>
            </button>
          </li>
        ))}
      </ol>

      {/* ETAPA 1 - CRIAR ------------------------------------------------------------ */}
      {step === 1 && (
        <section className="st-panel st-postar-media">
          <h2>1. Selecione a mídia</h2>
          <div className="st-postar-editor">
            <div className="st-postar-rail">
              {chosen.map((m, i) => (
                <button key={m.path} type="button" className={i === Math.min(slide, chosen.length - 1) ? "on" : ""} onClick={() => setSlide(i)} aria-label={`Ver ${m.title}`}>
                  {m.kind === "image" ? <img src={m.url} alt="" /> : <video src={`${m.url}#t=0.1`} muted preload="metadata" />}
                </button>
              ))}
              <button type="button" className="st-postar-add" onClick={() => setPicking(true)}>
                <Icon name="plus" size={18} />
                Adicionar
              </button>
            </div>
            <div className="st-postar-canvas">
              <div className="st-postar-tools">
                <span>
                  <Icon name="grid" size={16} /> Recortar (padrão das publicações)
                </span>
                <div className="st-seg">
                  {(["original", ...ASPECTS] as const).map((a) => (
                    <button key={a} type="button" className={aspect === a ? "active" : ""} onClick={() => setAspect(a)}>
                      {a === "original" ? "Original" : a}
                    </button>
                  ))}
                </div>
              </div>
              <div className="st-postar-stage">
                {current ? (
                  <div
                    className={aspect !== "original" ? "st-builder-frame cropped" : "st-builder-frame"}
                    style={aspect !== "original" ? { aspectRatio: String(aspectRatio(aspect)) } : undefined}
                  >
                    {current.kind === "image" ? <img src={current.url} alt="" /> : <video src={current.url} controls playsInline />}
                    {chosen.length > 1 && (
                      <>
                        <button type="button" className="st-car st-car-prev" onClick={() => setSlide((s) => (s - 1 + chosen.length) % chosen.length)} aria-label="Anterior">
                          <Icon name="chevronLeft" size={16} />
                        </button>
                        <button type="button" className="st-car st-car-next" onClick={() => setSlide((s) => (s + 1) % chosen.length)} aria-label="Próxima">
                          <Icon name="chevronRight" size={16} />
                        </button>
                      </>
                    )}
                  </div>
                ) : (
                  <button type="button" className="st-postar-empty" onClick={() => setPicking(true)}>
                    <Icon name="image" size={32} />
                    Escolha fotos ou vídeos da biblioteca
                  </button>
                )}
              </div>
            </div>
          </div>
          {chosen.length > 0 && (
            <>
              <span className="st-label">Mídias nesta sessão (a ordem vale para o carrossel)</span>
              <ul className="st-postar-strip">
                {chosen.map((m, i) => (
                  <li key={m.path}>
                    <button type="button" className="st-postar-strip-thumb" onClick={() => setSlide(i)} aria-label={`Ver ${m.title}`}>
                      {m.kind === "image" ? <img src={m.url} alt="" /> : <video src={`${m.url}#t=0.1`} muted preload="metadata" />}
                      <span>{i + 1}</span>
                      {cover === i && chosen.length > 1 && <em>Capa</em>}
                    </button>
                    <div>
                      <button type="button" className="st-icon-btn" onClick={() => setCover(i)} disabled={cover === i} aria-label="Usar como capa do carrossel" title="Capa do carrossel">
                        <Icon name="star" size={14} />
                      </button>
                      <button type="button" className="st-icon-btn" onClick={() => move(i, -1)} disabled={i === 0} aria-label="Mover para a esquerda">
                        <Icon name="chevronLeft" size={14} />
                      </button>
                      <button type="button" className="st-icon-btn" onClick={() => move(i, 1)} disabled={i === chosen.length - 1} aria-label="Mover para a direita">
                        <Icon name="chevronRight" size={14} />
                      </button>
                      <button type="button" className="st-icon-btn" onClick={() => removeAt(i)} aria-label="Tirar da sessão">
                        <Icon name="close" size={14} />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
          <div className="st-composer-actions">
            <button type="button" className="st-btn st-btn-primary" disabled={!chosen.length} onClick={() => setStep(2)}>
              Avançar: escolher redes
              <Icon name="chevronRight" size={16} />
            </button>
          </div>
        </section>
      )}

      {/* ETAPA 2 - CONFIGURAR ---------------------------------------------------------- */}
      {step === 2 && (
        <div className="st-postar-two">
          <section className="st-panel">
            <h2>2. Onde vai cada mídia?</h2>
            <p className="st-hint">Marque só onde cada mídia deve sair. Cada marcação vira uma publicação separada, com legenda e recorte próprios.</p>
            <div className="st-grid-wrap">
              <table className="st-pubgrid">
                <thead>
                  <tr>
                    <th>Mídia</th>
                    {SLOTS.filter((s) => platforms[s.network].on).map((s) => (
                      <th key={s.id}>
                        <Icon name={NETWORKS[s.network].icon} size={14} /> {s.label}
                        <small>{s.hint}</small>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {units.map((u) => (
                    <tr key={u.id}>
                      <td>
                        <span className="st-pubgrid-media">
                          {u.media[0] && (u.media[0].kind === "image" ? <img src={u.media[0].url} alt="" /> : <video src={`${u.media[0].url}#t=0.1`} muted preload="metadata" />)}
                          {u.label}
                        </span>
                      </td>
                      {SLOTS.filter((s) => platforms[s.network].on).map((s) => {
                        const why = incompatible(s.id, u.media);
                        const on = pubs.some((p) => p.key === `${s.id}:${u.id}`);
                        return (
                          <td key={s.id}>
                            <button
                              type="button"
                              className={on ? "st-cell on" : "st-cell"}
                              disabled={Boolean(why) && !on}
                              title={why ?? (on ? "Tirar desta rede" : "Publicar aqui")}
                              aria-pressed={on}
                              aria-label={`${u.label} em ${NETWORKS[s.network].label} ${s.label}`}
                              onClick={() => togglePub(s.id, u.id)}
                            >
                              {on ? <Icon name="check" size={14} /> : why ? "—" : ""}
                            </button>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {active.length > 0 && (
              <>
                <span className="st-label">Publicações ({active.length})</span>
                <div className="st-pubs">
                  {active.map((p) => {
                    const s = slotOf(p.slot);
                    const m = unitMedia(p.unit);
                    const probs = pubProblems(p);
                    const translated = translateFlags(s.network, [formatOf(p.slot, m)], pubMode(p), pubFlags(p));
                    return (
                      <article key={p.key} className={`st-pub st-target-${s.network}`}>
                        <header>
                          <span className="st-pub-thumbs">
                            {m.slice(0, 4).map((x) => (x.kind === "image" ? <img key={x.path} src={x.url} alt="" /> : <video key={x.path} src={`${x.url}#t=0.1`} muted preload="metadata" />))}
                          </span>
                          <b>
                            {NETWORKS[s.network].label} · {s.label}
                          </b>
                          <small>{m.length > 1 ? `carrossel de ${m.length}` : m[0]?.title}</small>
                          <button type="button" className="st-icon-btn" onClick={() => togglePub(p.slot, p.unit)} aria-label="Tirar esta publicação">
                            <Icon name="close" size={14} />
                          </button>
                        </header>
                        <div className="st-row">
                          <span className="st-hint">Recorte</span>
                          <div className="st-seg">
                            {(["original", ...ASPECTS] as const).map((a) => (
                              <button key={a} type="button" className={p.aspect === a ? "active" : ""} onClick={() => patchPub(p.key, { aspect: a })}>
                                {a === "original" ? "Original" : a}
                              </button>
                            ))}
                          </div>
                        </div>
                        <label className="st-check">
                          <input type="checkbox" checked={p.ownCaption} onChange={(e) => patchPub(p.key, { ownCaption: e.target.checked, caption: e.target.checked ? pubCaption(p) : p.caption })} />
                          Legenda própria nesta publicação
                        </label>
                        {p.ownCaption && (
                          <div className="st-textarea">
                            <textarea rows={3} value={p.caption} onChange={(e) => patchPub(p.key, { caption: e.target.value })} aria-label={`Legenda de ${NETWORKS[s.network].label} ${s.label}`} />
                            <span className="st-count">
                              {p.caption.length}/{NETWORKS[s.network].captionMax}
                            </span>
                          </div>
                        )}
                        {p.slot === "ig_reels" && (
                          <IgAudioPicker
                            value={p.igAudio}
                            enabled={instagram?.provider === "facebook_login"}
                            onChange={(a) => patchPub(p.key, { igAudio: a })}
                          />
                        )}
                        {translated.length > 0 && (
                          <ul className="st-translate">
                            {translated.map((x) => (
                              <li key={x.flag} className={x.how}>
                                <b>{x.label}:</b> {x.how === "automatico" ? "automático" : "no app"} — {x.note}
                              </li>
                            ))}
                          </ul>
                        )}
                        {probs.map((t) => (
                          <p key={t} className="st-error">
                            {t}
                            {t.includes("4:5 até 1,91:1") && (
                              <button type="button" className="st-btn st-btn-small" onClick={() => patchPub(p.key, { aspect: "4:5" })}>
                                Adaptar para 4:5
                              </button>
                            )}
                          </p>
                        ))}
                      </article>
                    );
                  })}
                </div>
              </>
            )}
          </section>

          <div className="st-postar-config">
            {PLATFORMS.map((n) => {
              const plat = platforms[n];
              const net = NETWORKS[n];
              const account = n === "instagram" ? (instagram?.connected ? `@${instagram.username}` : null) : n === "tiktok" ? (tiktok?.connected ? tiktok.name : null) : "lembrete";
              return (
                <section key={n} className={`st-panel st-target st-target-${n}${plat.on ? " on" : ""}`}>
                  <div className="st-target-head">
                    <Icon name={net.icon} size={18} />
                    <b>{net.label}</b>
                    <span className={account ? "st-hint" : "st-error"}>{account ?? "desconectado"}</span>
                    <label className="st-switch" title={plat.on ? "Desligar" : "Ligar"}>
                      <input type="checkbox" checked={plat.on} onChange={(e) => setPlatform(n, { on: e.target.checked })} aria-label={`Usar ${net.label}`} />
                      <span />
                    </label>
                  </div>
                  {plat.on && (
                    <>
                      {n !== "telegram" && (
                        <label className="st-check">
                          <input type="checkbox" checked={plat.auto} onChange={(e) => setPlatform(n, { auto: e.target.checked })} />
                          {n === "tiktok" ? "Enviar rascunho automático (você põe a música e publica no app)" : "Publicar automaticamente"}
                        </label>
                      )}
                      <div className="st-textarea">
                        <textarea rows={3} value={plat.caption} onChange={(e) => setPlatform(n, { caption: e.target.value })} placeholder={`Legenda do ${net.label}`} aria-label={`Legenda do ${net.label}`} />
                        <span className={plat.caption.length > net.captionMax ? "st-count over" : "st-count"}>
                          {plat.caption.length}/{net.captionMax}
                        </span>
                      </div>
                      {n === "instagram" && platforms.tiktok.on && (
                        <button type="button" className="st-btn st-btn-small" onClick={() => setPlatform("tiktok", { caption: plat.caption, hashtags: plat.hashtags })}>
                          Copiar legenda e hashtags para o TikTok
                        </button>
                      )}
                      <div className="st-tags">
                        {plat.hashtags.map((t) => (
                          <button key={t} type="button" className="st-tag" onClick={() => setPlatform(n, { hashtags: plat.hashtags.filter((x) => x !== t) })} title="Remover">
                            {t} ×
                          </button>
                        ))}
                        <input
                          className="st-tag-input"
                          value={tagInputs[n]}
                          onChange={(e) => setTagInputs((t) => ({ ...t, [n]: e.target.value }))}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === ",") {
                              e.preventDefault();
                              addTags(n);
                            }
                          }}
                          onBlur={() => addTags(n)}
                          placeholder="+ hashtag"
                          aria-label={`Nova hashtag do ${net.label}`}
                        />
                      </div>
                      {n !== "telegram" && (
                        <div className="st-row">
                          <Icon name="pin" size={16} />
                          <input className="st-input st-grow" value={plat.location} onChange={(e) => setPlatform(n, { location: e.target.value })} placeholder="Localização (opcional)" aria-label={`Localização no ${net.label}`} />
                        </div>
                      )}
                    </>
                  )}
                </section>
              );
            })}

            <section className="st-panel">
              <h2>Música e opções</h2>
              <input className="st-input" list="st-songs" value={music} onChange={(e) => setMusic(e.target.value)} placeholder="Música sugerida (opcional)" aria-label="Música" />
              <datalist id="st-songs">
                {songs.map((s) => (
                  <option key={s.id} value={`${s.name} — ${s.artist}`} />
                ))}
              </datalist>
              {(() => {
                const song = songs.find((s) => `${s.name} — ${s.artist}` === music);
                return song ? (
                  <div className="st-song">
                    <img src={song.artwork} alt="" />
                    <span>
                      <b>{song.name}</b>
                      <small>{song.artist}</small>
                    </span>
                    {song.previewUrl && (
                      <button type="button" className="st-btn st-btn-small" onClick={() => playSong(song)}>
                        Ouvir prévia
                      </button>
                    )}
                  </div>
                ) : null;
              })()}
              <div className="st-flags">
                <label className="st-check">
                  <input type="checkbox" checked={aiGenerated} onChange={(e) => setAiGenerated(e.target.checked)} />
                  Feito com IA
                </label>
                <label className="st-check">
                  <input type="checkbox" checked={commentsOff} onChange={(e) => setCommentsOff(e.target.checked)} />
                  Desligar comentários
                </label>
              </div>
            </section>
            <div className="st-composer-actions">
              <button type="button" className="st-btn" onClick={() => setStep(1)}>
                <Icon name="chevronLeft" size={16} />
                Voltar
              </button>
              <button type="button" className="st-btn st-btn-primary" disabled={!active.length} onClick={() => setStep(3)}>
                Avançar: revisar
                <Icon name="chevronRight" size={16} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ETAPA 3 - REVISAR --------------------------------------------------------------- */}
      {step === 3 && (
        <div className="st-postar-two">
          <section className="st-panel st-postar-preview">
            <h2>3. Pré-visualização</h2>
            <div className="st-seg">
              {SLOTS.filter((s) => s.id !== "tg").map((s) => {
                const count = active.filter((p) => p.slot === s.id).length;
                return (
                  <button key={s.id} type="button" className={shownSlot === s.id ? "active" : ""} onClick={() => setPreviewSlot(s.id)}>
                    {s.network === "instagram" ? `Instagram ${s.label}` : s.label} ({count})
                  </button>
                );
              })}
            </div>
            {previewPubs.length > 1 && (
              <div className="st-seg">
                {previewPubs.map((p, i) => (
                  <button key={p.key} type="button" className={shownPub?.key === p.key ? "active" : ""} onClick={() => setPreviewKey(p.key)}>
                    {i + 1}
                  </button>
                ))}
              </div>
            )}
            {shownPub ? (
              <PhonePreview
                key={shownPub.key}
                network={slotOf(shownPub.slot).network}
                format={formatOf(shownPub.slot, unitMedia(shownPub.unit))}
                media={unitMedia(shownPub.unit)}
                coverIndex={shownPub.unit === CAROUSEL ? cover : 0}
                aspect={shownPub.aspect}
                caption={pubCaption(shownPub)}
                hashtags={platforms[slotOf(shownPub.slot).network].hashtags}
                user={slotOf(shownPub.slot).network === "instagram" ? instagram?.username ?? "lunavox" : tiktok?.name ?? "lunavox"}
                music={shownPub.igAudio ? [shownPub.igAudio.title, shownPub.igAudio.artist].filter(Boolean).join(" — ") : music}
                location={platforms[slotOf(shownPub.slot).network].location}
              />
            ) : (
              <p className="st-empty">Nada marcado para {slotOf(shownSlot).network === "instagram" ? `o Instagram ${slotOf(shownSlot).label}` : "o TikTok"}.</p>
            )}
          </section>
          <section className="st-panel">
            <h2>Alertas</h2>
            {blockingCount === 0 ? <p className="st-ok">Nenhuma pendência: dá para publicar.</p> : <p className="st-error">{blockingCount} pendência(s) para corrigir:</p>}
            <ul className="st-alerts">
              {active.flatMap((p) =>
                pubProblems(p).map((t) => (
                  <li key={`${p.key}-${t}`} className="bad">
                    <b>
                      {NETWORKS[slotOf(p.slot).network].label} {slotOf(p.slot).label}:
                    </b>{" "}
                    {t}
                  </li>
                ))
              )}
              {scheduleProblem && <li className="bad">Agendamento: {scheduleProblem}</li>}
              {warnings.map((w) => (
                <li key={w} className="info">
                  {w}
                </li>
              ))}
            </ul>
            <div className="st-composer-actions">
              <button type="button" className="st-btn" onClick={() => setStep(2)}>
                <Icon name="chevronLeft" size={16} />
                Voltar e corrigir
              </button>
              <button type="button" className="st-btn st-btn-primary" onClick={() => setStep(4)}>
                Avançar: publicar
                <Icon name="chevronRight" size={16} />
              </button>
            </div>
          </section>
        </div>
      )}

      {/* ETAPA 4 - PUBLICAR ------------------------------------------------------------ */}
      {step === 4 && (
        <section className="st-panel">
          <h2>4. Publicar</h2>
          <div className="st-row">
            <label className="st-check">
              <input type="radio" name="st-when" checked={when === "agora"} onChange={() => setWhen("agora")} />
              Publicar agora
            </label>
            <label className="st-check">
              <input type="radio" name="st-when" checked={when === "depois"} onChange={() => setWhen("depois")} />
              Agendar para depois
            </label>
            {when === "depois" && (
              <>
                <input type="date" className="st-input" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Dia" />
                <input type="time" className="st-input" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Hora" />
              </>
            )}
            <span className="st-hint">fuso: {timeZone}</span>
          </div>
          <table className="st-summary">
            <thead>
              <tr>
                <th>Rede</th>
                <th>Formato</th>
                <th>Mídia</th>
                <th>Legenda</th>
                <th>Como</th>
                <th>Resultado</th>
              </tr>
            </thead>
            <tbody>
              {active.map((p) => {
                const s = slotOf(p.slot);
                const m = unitMedia(p.unit);
                const r = results.find((x) => x.key === p.key);
                const text = [pubCaption(p), platforms[s.network].hashtags.join(" ")].filter(Boolean).join(" ");
                return (
                  <tr key={p.key}>
                    <td>
                      <Icon name={NETWORKS[s.network].icon} size={14} /> {NETWORKS[s.network].label}
                    </td>
                    <td>
                      {formatOf(p.slot, m)}
                      {p.aspect !== "original" ? ` · ${p.aspect}` : ""}
                    </td>
                    <td>
                      <span className="st-pub-thumbs">
                        {m.slice(0, 4).map((x) => (x.kind === "image" ? <img key={x.path} src={x.url} alt="" /> : <video key={x.path} src={`${x.url}#t=0.1`} muted preload="metadata" />))}
                      </span>
                    </td>
                    <td className="st-summary-caption">{text || "—"}</td>
                    <td>{pubMode(p) === "api" ? (s.network === "tiktok" ? "rascunho automático" : "automático") : "requer ação manual"}</td>
                    <td className={r ? (r.ok ? "st-ok" : "st-error") : ""}>{r ? r.text : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {message && <p className={message.ok ? "st-ok" : "st-error"}>{message.text}</p>}
          <div className="st-composer-actions">
            <button type="button" className="st-btn" onClick={() => setStep(3)} disabled={busy}>
              <Icon name="chevronLeft" size={16} />
              Voltar
            </button>
            <button type="button" className="st-btn" onClick={() => finish("rascunho")} disabled={busy}>
              Salvar rascunho
            </button>
            <button type="button" className="st-btn" onClick={() => finish("aprovacao")} disabled={busy || blockingCount > 0}>
              Enviar para aprovação
            </button>
            <button type="button" className="st-btn" onClick={() => finish("telegram")} disabled={busy || blockingCount > 0}>
              <Icon name="telegram" size={16} />
              Aprovar pelo Telegram
            </button>
            <button type="button" className="st-btn st-btn-primary" onClick={() => (blockingCount ? setStep(3) : finish("publicar"))} disabled={busy}>
              <Icon name={blockingCount ? "close" : "send"} size={16} />
              {primaryLabel}
            </button>
          </div>
        </section>
      )}

      {picking && (
        <LibraryPicker
          selected={media}
          onClose={() => setPicking(false)}
          onConfirm={(paths) => {
            setMedia(paths);
            setSlide(0);
            setCover(0);
            // publicacoes de midias que sairam da sessao saem junto
            setPubs((list) => list.filter((p) => p.unit === CAROUSEL ? paths.length > 1 : paths.includes(p.unit)));
            setPicking(false);
          }}
        />
      )}
    </div>
  );
}

interface IgAudioResult {
  id: string;
  title: string;
  artist: string;
  durationMs: number | null;
  cover: string | null;
  previewLink: string | null;
}

// Musica da biblioteca do Instagram para um Reels (Audio API da Meta, so pela
// conexao do Facebook). Sem busca = em alta. O Instagram aplica a musica ao
// publicar; a previa so existe no app (link "ouvir no Instagram").
function IgAudioPicker({ value, enabled, onChange }: { value?: IgAudioFlag; enabled: boolean; onChange: (a?: IgAudioFlag) => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<IgAudioResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!enabled) {
    return <p className="st-hint">Música do Instagram: conecte pelo Facebook (página Instagram) para escolher a música deste Reels.</p>;
  }
  async function search(q: string) {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`/api/studio?r=instagram&action=audio&q=${encodeURIComponent(q)}`);
      const b = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
      setResults(b.audio ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="st-igaudio">
      <span className="st-hint">Música do Instagram</span>
      {value ? (
        <div className="st-song">
          <span>
            <b>{value.title}</b>
            <small>{value.artist}</small>
          </span>
          <button type="button" className="st-btn st-btn-small" onClick={() => onChange(undefined)}>
            Trocar
          </button>
        </div>
      ) : (
        <>
          <div className="st-row">
            <input
              className="st-input st-grow"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), search(query))}
              placeholder="Buscar música ou artista"
              aria-label="Buscar música do Instagram"
            />
            <button type="button" className="st-btn st-btn-small" disabled={busy} onClick={() => search(query)}>
              Buscar
            </button>
            <button type="button" className="st-btn st-btn-small" disabled={busy} onClick={() => search("")}>
              Em alta
            </button>
          </div>
          {error && <p className="st-error">{error}</p>}
          {results && results.length === 0 && <p className="st-hint">Nenhuma música liberada para essa busca.</p>}
          {results && results.length > 0 && (
            <ul className="st-igaudio-list">
              {results.map((a) => (
                <li key={a.id}>
                  {a.cover ? <img src={a.cover} alt="" /> : <span className="st-igaudio-cover" />}
                  <span>
                    <b>{a.title}</b>
                    <small>
                      {a.artist}
                      {a.durationMs ? ` · ${Math.floor(a.durationMs / 60000)}:${String(Math.round((a.durationMs % 60000) / 1000)).padStart(2, "0")}` : ""}
                    </small>
                  </span>
                  {a.previewLink && (
                    <a className="st-link" href={a.previewLink} target="_blank" rel="noreferrer">
                      ouvir no Instagram
                    </a>
                  )}
                  <button type="button" className="st-btn st-btn-small" onClick={() => onChange({ id: a.id, title: a.title, artist: a.artist })}>
                    Usar
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function LibraryPicker({ selected, onClose, onConfirm }: { selected: string[]; onClose: () => void; onConfirm: (paths: string[]) => void }) {
  const { items } = useStudio();
  const [picked, setPicked] = useState<string[]>(selected);
  const library = items.filter((i) => i.display !== "arquivado");
  return (
    <Modal title="Escolher mídias" onClose={onClose} wide>
      {library.length === 0 ? (
        <div className="st-empty">A biblioteca está vazia. Adicione arquivos em Conteúdos.</div>
      ) : (
        <div className="st-picker">
          {library.map((item) => {
            const n = picked.indexOf(item.path);
            return (
              <div key={item.path} className={n >= 0 ? "st-pick on" : "st-pick"}>
                <Thumb item={item} onClick={() => setPicked((p) => (n >= 0 ? p.filter((x) => x !== item.path) : [...p, item.path]))} />
                {n >= 0 && <span className="st-pick-n">{n + 1}</span>}
                <span className="st-pick-name">{item.title}</span>
              </div>
            );
          })}
        </div>
      )}
      <div className="st-composer-actions">
        <span className="st-hint">Toque na ordem em que devem aparecer.</span>
        <button type="button" className="st-btn" onClick={onClose}>
          Cancelar
        </button>
        <button type="button" className="st-btn st-btn-primary" onClick={() => onConfirm(picked)} disabled={!picked.length}>
          Usar {picked.length} {picked.length === 1 ? "mídia" : "mídias"}
        </button>
      </div>
    </Modal>
  );
}

// Como a publicacao aparece no celular, com as midias, a ordem, o recorte e a
// legenda reais dela (aproximado: cada app tem detalhes proprios).
function PhonePreview(props: {
  network: Network;
  format: string;
  media: Item[];
  coverIndex: number;
  aspect: Aspect | "original";
  caption: string;
  hashtags: string[];
  user: string;
  music: string;
  location: string;
}) {
  const { network, format, media, coverIndex, aspect, caption, hashtags, user, music, location } = props;
  // a ordem e a real; no TikTok a previa abre na foto de capa (no Instagram a
  // capa do carrossel e sempre a primeira midia)
  const [i, setI] = useState(network === "tiktok" && coverIndex < media.length ? coverIndex : 0);
  const ordered = media;
  const item = ordered[Math.min(i, ordered.length - 1)];
  const vertical = network === "tiktok" || format === "Stories" || format === "Reels";
  const text = [caption, hashtags.join(" ")].filter(Boolean).join(" ");
  const shown = item ? (
    item.kind === "image" ? <img src={item.url} alt="" /> : <video src={`${item.url}#t=0.1`} muted preload="metadata" />
  ) : (
    <div className="st-phone-blank">
      <Icon name="image" size={28} />
    </div>
  );
  const feedRatio = aspect === "1:1" ? "1" : aspect === "9:16" ? "0.5625" : "0.8";
  const nav =
    ordered.length > 1 ? (
      <>
        <button type="button" className="st-car st-car-prev" onClick={() => setI((x) => (x - 1 + ordered.length) % ordered.length)} aria-label="Anterior">
          <Icon name="chevronLeft" size={14} />
        </button>
        <button type="button" className="st-car st-car-next" onClick={() => setI((x) => (x + 1) % ordered.length)} aria-label="Próxima">
          <Icon name="chevronRight" size={14} />
        </button>
        <span className="st-builder-count">
          {Math.min(i, ordered.length - 1) + 1}/{ordered.length}
        </span>
      </>
    ) : null;
  return (
    <div className="st-phone">
      <div className="st-phone-bar">
        <span>9:41</span>
        <span>●●●</span>
      </div>
      {vertical ? (
        <div className={`st-phone-vertical st-phone-${network}`}>
          {shown}
          <div className="st-phone-top">
            <span className="st-phone-avatar" />
            <b>{user}</b>
            {format === "Stories" && <small>agora</small>}
          </div>
          {format === "Stories" ? (
            <div className="st-phone-reply">Enviar mensagem...</div>
          ) : (
            <div className="st-phone-caption">
              <b>@{user.replace(/^@/, "")}</b>
              <p>{text || "Sua legenda aparece aqui"}</p>
              {music && <small>♪ {music}</small>}
            </div>
          )}
          {nav}
        </div>
      ) : (
        <div className="st-phone-feed">
          <div className="st-phone-top dark">
            <span className="st-phone-avatar" />
            <span>
              <b>{user}</b>
              {location && <small>{location}</small>}
            </span>
          </div>
          <div className="st-phone-feed-media" style={{ aspectRatio: feedRatio }}>
            {shown}
            {nav}
          </div>
          <div className="st-phone-actions">
            <Icon name="heart" size={18} />
            <Icon name="comment" size={18} />
            <Icon name="send" size={18} />
          </div>
          <p className="st-phone-text">
            <b>{user}</b> {text || "Sua legenda aparece aqui"}
          </p>
        </div>
      )}
    </div>
  );
}

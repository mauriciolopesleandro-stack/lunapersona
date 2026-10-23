import { useEffect, useMemo, useState } from "react";
import { aspectRatio, ASPECTS, flagProblem, translateFlags, type Aspect, type PostFlags } from "../../api/_flags";
import { Icon } from "./Icon";
import { Modal, NETWORKS, Thumb } from "./shared";
import { useStudio, type Item, type Network, type Post } from "./store";

// Postar: tudo numa tela so. A esquerda a midia (escolher, recortar, ordem e
// capa), no meio onde publicar, legenda por rede, musica, opcoes e horario, a
// direita a previa no celular. Uma publicacao pode ir para varias redes: o
// estudio cria uma por rede, ligadas pelo mesmo grupo, e as opcoes ("flags")
// sao traduzidas para cada rede (api/_flags.ts).

const MEDIA_MAX: Record<Network, number> = { instagram: 20, tiktok: 35, telegram: 10 };
const ORDER: Network[] = ["instagram", "tiktok", "telegram"];
const FORMAT_HINT: Record<string, string> = {
  Feed: "post no perfil",
  Stories: "some em 24 h",
  Reels: "vídeo vertical",
  Vídeo: "rascunho de vídeo",
  Foto: "rascunho de fotos",
  Canal: "canal",
  Grupo: "grupo",
};

interface Target {
  on: boolean;
  formats: string[];
  auto: boolean;
}

interface Song {
  id: string;
  name: string;
  artist: string;
  artwork: string;
  previewUrl: string | null;
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function localDate(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function suggestedFormats(network: Network, media: Item[]): string[] {
  const videos = media.filter((m) => m.kind === "video").length;
  if (network === "instagram") return media.length === 1 && videos === 1 ? ["Reels"] : ["Feed"];
  if (network === "tiktok") return videos ? ["Vídeo"] : ["Foto"];
  return ["Canal"];
}

// Problemas que impedem publicar numa rede (vazio = tudo certo).
function problemsFor(network: Network, t: Target, media: Item[], caption: string, flags: PostFlags): string[] {
  const out: string[] = [];
  const net = NETWORKS[network];
  const videos = media.filter((m) => m.kind === "video").length;
  if (!media.length) out.push("escolha pelo menos uma mídia");
  if (!t.formats.length) out.push("marque pelo menos um destino");
  if (caption.length > net.captionMax) out.push(`a legenda passou do limite (${caption.length}/${net.captionMax})`);
  if (media.length > MEDIA_MAX[network]) out.push(`no máximo ${MEDIA_MAX[network]} mídias`);
  for (const m of media) {
    if (m.rating === "adulto" && network !== "telegram") out.push(`“${m.title}” é conteúdo adulto (só Telegram)`);
    else if (m.destinations?.length && !m.destinations.includes(network)) out.push(`“${m.title}” não está liberado para ${net.label}`);
  }
  if (t.auto && network === "tiktok") {
    const video = t.formats.length === 1 && t.formats[0] === "Vídeo" && media.length === 1 && videos === 1;
    const photos = t.formats.length === 1 && t.formats[0] === "Foto" && media.length >= 1 && videos === 0;
    if (media.length && !video && !photos) out.push("automático: 1 vídeo no destino Vídeo, ou só fotos no destino Foto");
  }
  if (t.auto && network === "instagram") {
    if (t.formats.includes("Reels") && (media.length !== 1 || videos !== 1)) out.push("Reels automático precisa de exatamente 1 vídeo");
    if (t.formats.includes("Stories") && media.length !== 1) out.push("Stories automático precisa de exatamente 1 mídia");
  }
  const issue = flagProblem(network, t.formats, flags);
  if (issue) out.push(issue);
  return out;
}

export interface PostarStart {
  key: number;
  date?: Date;
  media?: string[];
}

export function PostarPage({ start, onDone }: { start: PostarStart; onDone: () => void }) {
  const { items, saveCloudPost, postAction, approveAutomatic, instagram, tiktok, state, source } = useStudio();
  const [media, setMedia] = useState<string[]>(start.media ?? []);
  const [slide, setSlide] = useState(0);
  const [aspect, setAspect] = useState<Aspect | "original">("original");
  const [cover, setCover] = useState(0);
  const [picking, setPicking] = useState(false);
  const [targets, setTargets] = useState<Record<Network, Target>>({
    instagram: { on: true, formats: ["Feed"], auto: true },
    tiktok: { on: false, formats: ["Foto"], auto: true },
    telegram: { on: false, formats: ["Canal"], auto: false },
  });
  const [sameCaption, setSameCaption] = useState(true);
  const [captions, setCaptions] = useState<Record<Network, string>>({ instagram: "", tiktok: "", telegram: "" });
  const [captionTab, setCaptionTab] = useState<Network>("instagram");
  const [hashtags, setHashtags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [music, setMusic] = useState("");
  const [songs, setSongs] = useState<Song[]>([]);
  const [playing, setPlaying] = useState<HTMLAudioElement | null>(null);
  const [location, setLocation] = useState("");
  const [aiGenerated, setAiGenerated] = useState(false);
  const [commentsOff, setCommentsOff] = useState(false);
  const [when, setWhen] = useState<"agora" | "depois">(start.date ? "depois" : "agora");
  const startDate = start.date ?? new Date(Date.now() + 3600_000);
  const [date, setDate] = useState(localDate(startDate));
  const [time, setTime] = useState(`${pad(startDate.getHours())}:${pad(startDate.getMinutes())}`);
  const [preview, setPreview] = useState<{ network: Network; format: string }>({ network: "instagram", format: "Feed" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  // musicas em alta (Apple Music Brasil) para sugerir a trilha
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
  const enabled = ORDER.filter((n) => targets[n].on);
  const captionOf = (n: Network) => (sameCaption ? captions.instagram : captions[n]);
  const flags: PostFlags = {
    ...(aiGenerated ? { aiGenerated: true } : {}),
    ...(music.trim() ? { music: music.trim() } : {}),
    ...(commentsOff ? { commentsOff: true } : {}),
    ...(cover > 0 && cover < chosen.length ? { coverIndex: cover } : {}),
    ...(aspect !== "original" ? { aspect } : {}),
    ...(location.trim() ? { location: location.trim() } : {}),
  };
  const problems = Object.fromEntries(
    ORDER.map((n) => [n, targets[n].on ? problemsFor(n, targets[n], chosen, captionOf(n), flags) : []])
  ) as Record<Network, string[]>;
  const blocked = !enabled.length || enabled.some((n) => problems[n].length > 0);
  const frame = aspect === "original" ? undefined : String(aspectRatio(aspect));
  const step = !chosen.length ? 1 : blocked ? 2 : 3;

  // ao trocar as midias, sugere os formatos de cada rede
  useEffect(() => {
    if (!chosen.length) return;
    setTargets((t) => {
      const next = { ...t };
      for (const n of ORDER) next[n] = { ...t[n], formats: suggestedFormats(n, chosen) };
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [media.join("|")]);

  // previa acompanha as redes ligadas
  useEffect(() => {
    if (!targets[preview.network].on || !targets[preview.network].formats.includes(preview.format)) {
      const n = enabled[0];
      if (n) setPreview({ network: n, format: targets[n].formats[0] ?? NETWORKS[n].formats[0] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(targets)]);

  useEffect(() => {
    if (!targets[captionTab].on && enabled[0]) setCaptionTab(enabled[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled.join("|")]);

  if (source !== "banco") {
    return <div className="st-empty">Para postar, ligue o banco de dados (Configurações).</div>;
  }
  if (state !== "ready") {
    return <div className="st-empty">Abra a pasta do estúdio (barra lateral) para escolher as mídias.</div>;
  }

  function setTarget(n: Network, patch: Partial<Target>) {
    setTargets((t) => ({ ...t, [n]: { ...t[n], ...patch } }));
  }

  function setCaption(text: string) {
    if (sameCaption) setCaptions({ instagram: text, tiktok: text, telegram: text });
    else setCaptions((c) => ({ ...c, [captionTab]: text }));
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
    setMedia((m) => m.filter((_, k) => k !== i));
    setCover((c) => (c === i ? 0 : c > i ? c - 1 : c));
    setSlide(0);
  }

  function addTag() {
    const tags = tagInput
      .split(/[\s,]+/)
      .map((t) => t.replace(/^#+/, "").trim())
      .filter(Boolean)
      .map((t) => `#${t}`);
    if (tags.length) setHashtags((h) => [...h, ...tags.filter((t) => !h.includes(t))]);
    setTagInput("");
  }

  function playSong(song: Song) {
    playing?.pause();
    if (!song.previewUrl) return;
    const a = new Audio(song.previewUrl);
    a.play().catch(() => undefined);
    setPlaying(a);
  }

  // mode: "rascunho" so salva; "aprovacao" envia para aprovar depois;
  // "publicar" salva, envia e aprova na hora (o clique e a aprovacao).
  async function finish(mode: "rascunho" | "aprovacao" | "publicar") {
    setMessage(null);
    if (blocked) return setMessage({ ok: false, text: "Corrija os avisos das redes marcadas." });
    const at = when === "agora" ? new Date() : date && time ? new Date(`${date}T${time}`) : null;
    if (mode !== "rascunho" && !at) return setMessage({ ok: false, text: "Escolha o dia e a hora." });
    if (mode === "publicar") {
      const where = enabled.map((n) => `${NETWORKS[n].label} (${targets[n].formats.join(", ")}${targets[n].auto ? ", automático" : ", manual"})`).join(" e ");
      const hora = when === "agora" ? "agora" : at!.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
      if (!window.confirm(`Aprovar e publicar ${hora} em ${where}?`)) return;
    }
    const groupId = enabled.length > 1 ? crypto.randomUUID() : null;
    setBusy(true);
    const done: string[] = [];
    try {
      for (const n of enabled) {
        const t = targets[n];
        const label = NETWORKS[n].label;
        setMessage({ ok: true, text: `${label}: salvando...` });
        const { post } = await saveCloudPost({
          network: n,
          formats: t.formats,
          media,
          caption: captionOf(n),
          hashtags,
          scheduledAt: at ? at.toISOString() : null,
          publishMode: n !== "telegram" && t.auto ? "api" : "manual",
          flags,
          groupId,
        });
        if (mode !== "rascunho") await postAction("submit", post.id);
        if (mode === "publicar") {
          const ready: Post = { ...post, status: "aguardando_aprovacao" };
          if (ready.publishMode === "api") await approveAutomatic(ready, (text) => setMessage({ ok: true, text: `${label}: ${text}` }));
          else await postAction("approve", post.id, { version: post.version });
        }
        done.push(label);
      }
      setMessage({
        ok: true,
        text:
          mode === "publicar"
            ? `Aprovado em ${done.join(", ")}. ${when === "agora" ? "O envio sai no próximo minuto" : "No horário o estúdio envia"}; acompanhe em Publicações.`
            : mode === "aprovacao"
              ? `Enviado para aprovação (${done.join(", ")}). Aprove em Publicações.`
              : `Rascunho salvo (${done.join(", ")}).`,
      });
      setTimeout(onDone, 1800);
    } catch (e) {
      const err = e instanceof Error ? e.message : String(e);
      setMessage({ ok: false, text: done.length ? `Feito em ${done.join(", ")}, mas parou na próxima rede: ${err}` : err });
    } finally {
      setBusy(false);
    }
  }

  const steps = [
    ["Criar", "Escolher mídia"],
    ["Configurar", "Redes e legendas"],
    ["Revisar", "Pré-visualização"],
    ["Publicar", "Enviar conteúdo"],
  ];

  return (
    <div className="st-postar">
      <ol className="st-steps-bar st-postar-steps" aria-label="Passos">
        {steps.map(([label, sub], i) => (
          <li key={label} className={step === i + 1 ? "on" : step > i + 1 ? "done" : ""}>
            <span>{i + 1}</span>
            <b>{label}</b>
            <small>{sub}</small>
          </li>
        ))}
      </ol>

      <div className="st-postar-grid">
        {/* 1. Midia ------------------------------------------------------------ */}
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
                  <Icon name="grid" size={16} /> Recortar
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
                  <div className={frame ? "st-builder-frame cropped" : "st-builder-frame"} style={frame ? { aspectRatio: frame } : undefined}>
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
              <span className="st-label">Mídias nesta publicação</span>
              <ul className="st-postar-strip">
                {chosen.map((m, i) => (
                  <li key={m.path}>
                    <button type="button" className="st-postar-strip-thumb" onClick={() => setSlide(i)} aria-label={`Ver ${m.title}`}>
                      {m.kind === "image" ? <img src={m.url} alt="" /> : <video src={`${m.url}#t=0.1`} muted preload="metadata" />}
                      <span>{i + 1}</span>
                      {cover === i && <em>Capa</em>}
                    </button>
                    <div>
                      <button type="button" className="st-icon-btn" onClick={() => setCover(i)} disabled={cover === i} aria-label="Usar como capa" title="Usar como capa">
                        <Icon name="star" size={14} />
                      </button>
                      <button type="button" className="st-icon-btn" onClick={() => move(i, -1)} disabled={i === 0} aria-label="Mover para a esquerda">
                        <Icon name="chevronLeft" size={14} />
                      </button>
                      <button type="button" className="st-icon-btn" onClick={() => move(i, 1)} disabled={i === chosen.length - 1} aria-label="Mover para a direita">
                        <Icon name="chevronRight" size={14} />
                      </button>
                      <button type="button" className="st-icon-btn" onClick={() => removeAt(i)} aria-label="Tirar da publicação">
                        <Icon name="close" size={14} />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>

        {/* 2-6. Configurar ------------------------------------------------------- */}
        <div className="st-postar-config">
          <section className="st-panel">
            <h2>2. Onde publicar?</h2>
            <div className="st-targets">
              {ORDER.map((n) => {
                const t = targets[n];
                const net = NETWORKS[n];
                const account = n === "instagram" ? (instagram?.connected ? `@${instagram.username}` : null) : n === "tiktok" ? (tiktok?.connected ? tiktok.name : null) : null;
                const translated = t.on ? translateFlags(n, t.formats, t.auto ? "api" : "manual", flags) : [];
                return (
                  <section key={n} className={`st-target st-target-${n}${t.on ? " on" : ""}`}>
                    <div className="st-target-head">
                      <Icon name={net.icon} size={18} />
                      <b>{net.label}</b>
                      <span className="st-hint">{account ?? (n === "telegram" ? "lembrete" : "não conectado")}</span>
                      <label className="st-switch" title={t.on ? "Desligar" : "Ligar"}>
                        <input type="checkbox" checked={t.on} onChange={(e) => setTarget(n, { on: e.target.checked })} aria-label={`Publicar no ${net.label}`} />
                        <span />
                      </label>
                    </div>
                    {t.on && (
                      <>
                        <div className="st-format-tiles">
                          {net.formats.map((f) => {
                            const on = t.formats.includes(f);
                            return (
                              <button
                                key={f}
                                type="button"
                                className={on ? "st-format on" : "st-format"}
                                onClick={() => setTarget(n, { formats: on ? t.formats.filter((x) => x !== f) : [...t.formats, f] })}
                                aria-pressed={on}
                              >
                                <span className="st-format-check">{on && <Icon name="check" size={12} />}</span>
                                <b>{f}</b>
                                <small>{FORMAT_HINT[f]}</small>
                                {chosen[cover] && chosen[cover].kind === "image" && <img src={chosen[cover].url} alt="" />}
                              </button>
                            );
                          })}
                        </div>
                        {n !== "telegram" && (
                          <label className="st-check">
                            <input type="checkbox" checked={t.auto} onChange={(e) => setTarget(n, { auto: e.target.checked })} />
                            {n === "tiktok" ? "Enviar rascunho automático (você põe a música e publica no app)" : "Publicar automaticamente"}
                          </label>
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
                        {problems[n].map((p) => (
                          <p key={p} className="st-error">
                            {p}
                          </p>
                        ))}
                      </>
                    )}
                  </section>
                );
              })}
            </div>
          </section>

          <section className="st-panel">
            <h2>3. Legenda e hashtags</h2>
            <label className="st-check">
              <input type="checkbox" checked={sameCaption} onChange={(e) => setSameCaption(e.target.checked)} />
              Mesma legenda em todas as redes
            </label>
            {!sameCaption && (
              <div className="st-seg st-caption-tabs">
                {enabled.map((n) => (
                  <button key={n} type="button" className={captionTab === n ? "active" : ""} onClick={() => setCaptionTab(n)}>
                    {NETWORKS[n].label}
                  </button>
                ))}
              </div>
            )}
            <div className="st-textarea">
              <textarea
                rows={4}
                value={sameCaption ? captions.instagram : captions[captionTab]}
                onChange={(e) => setCaption(e.target.value)}
                placeholder="Escreva a legenda..."
                aria-label="Legenda"
              />
              <span className="st-count">
                {enabled.map((n) => `${NETWORKS[n].label} ${captionOf(n).length}/${NETWORKS[n].captionMax}`).join(" · ")}
              </span>
            </div>
            <span className="st-label">Hashtags</span>
            <div className="st-tags">
              {hashtags.map((t) => (
                <button key={t} type="button" className="st-tag" onClick={() => setHashtags((h) => h.filter((x) => x !== t))} title="Remover">
                  {t} ×
                </button>
              ))}
              <input
                className="st-tag-input"
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    addTag();
                  }
                }}
                onBlur={addTag}
                placeholder="+ hashtag"
                aria-label="Nova hashtag"
              />
            </div>
          </section>

          <section className="st-panel">
            <h2>4. Música</h2>
            <div className="st-row">
              <input
                className="st-input st-grow"
                list="st-songs"
                value={music}
                onChange={(e) => setMusic(e.target.value)}
                placeholder="Escolha das músicas em alta ou digite"
                aria-label="Música"
              />
              <datalist id="st-songs">
                {songs.map((s) => (
                  <option key={s.id} value={`${s.name} — ${s.artist}`} />
                ))}
              </datalist>
            </div>
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
            <p className="st-hint">As APIs não colocam música: no TikTok você põe no rascunho; no Instagram, só publicando pelo app (modo manual).</p>
          </section>

          <section className="st-panel">
            <h2>5. Localização e opções</h2>
            <div className="st-row">
              <Icon name="pin" size={16} />
              <input className="st-input st-grow" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Ex.: Rio de Janeiro, RJ" aria-label="Localização" />
            </div>
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

          <section className="st-panel">
            <h2>6. Quando</h2>
            <div className="st-row">
              <label className="st-check">
                <input type="radio" name="st-when" checked={when === "agora"} onChange={() => setWhen("agora")} />
                Publicar agora
              </label>
              <label className="st-check">
                <input type="radio" name="st-when" checked={when === "depois"} onChange={() => setWhen("depois")} />
                Agendar para depois
              </label>
            </div>
            {when === "depois" && (
              <div className="st-row">
                <input type="date" className="st-input" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Dia" />
                <input type="time" className="st-input" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Hora" />
              </div>
            )}
          </section>
        </div>

        {/* 7. Previa --------------------------------------------------------------- */}
        <section className="st-panel st-postar-preview">
          <h2>7. Pré-visualização</h2>
          {enabled.length ? (
            <>
              <div className="st-seg">
                {enabled.map((n) => (
                  <button key={n} type="button" className={preview.network === n ? "active" : ""} onClick={() => setPreview({ network: n, format: targets[n].formats[0] ?? NETWORKS[n].formats[0] })}>
                    {NETWORKS[n].label}
                  </button>
                ))}
              </div>
              {targets[preview.network].formats.length > 1 && (
                <div className="st-seg">
                  {targets[preview.network].formats.map((f) => (
                    <button key={f} type="button" className={preview.format === f ? "active" : ""} onClick={() => setPreview({ ...preview, format: f })}>
                      {f}
                    </button>
                  ))}
                </div>
              )}
              <PhonePreview
                network={preview.network}
                format={preview.format}
                item={chosen[cover] ?? chosen[0]}
                count={chosen.length}
                aspect={aspect}
                caption={captionOf(preview.network)}
                hashtags={hashtags}
                user={preview.network === "instagram" ? instagram?.username ?? "lunavox" : preview.network === "tiktok" ? tiktok?.name ?? "lunavox" : "Canal"}
                music={music}
                location={location}
              />
            </>
          ) : (
            <p className="st-hint">Ligue pelo menos uma rede.</p>
          )}

          {message && <p className={message.ok ? "st-ok" : "st-error"}>{message.text}</p>}
          <button type="button" className="st-btn st-btn-primary st-postar-go" onClick={() => finish("publicar")} disabled={busy || blocked}>
            <Icon name="send" size={18} />
            {when === "agora" ? "Publicar agora" : "Agendar publicação"}
          </button>
          <div className="st-postar-more">
            <button type="button" className="st-btn st-btn-small" onClick={() => finish("aprovacao")} disabled={busy || blocked}>
              Enviar para aprovação
            </button>
            <button type="button" className="st-btn st-btn-small" onClick={() => finish("rascunho")} disabled={busy || blocked}>
              Salvar rascunho
            </button>
          </div>
        </section>
      </div>

      {picking && (
        <LibraryPicker
          selected={media}
          onClose={() => setPicking(false)}
          onConfirm={(paths) => {
            setMedia(paths);
            setSlide(0);
            setCover(0);
            setPicking(false);
          }}
        />
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

// Como o post aparece no celular (aproximado; cada app tem detalhes proprios).
function PhonePreview(props: {
  network: Network;
  format: string;
  item?: Item;
  count: number;
  aspect: Aspect | "original";
  caption: string;
  hashtags: string[];
  user: string;
  music: string;
  location: string;
}) {
  const { network, format, item, count, aspect, caption, hashtags, user, music, location } = props;
  const vertical = network === "tiktok" || format === "Stories" || format === "Reels" || format === "Vídeo";
  const text = [caption, hashtags.join(" ")].filter(Boolean).join(" ");
  const media = item ? (
    item.kind === "image" ? <img src={item.url} alt="" /> : <video src={`${item.url}#t=0.1`} muted preload="metadata" />
  ) : (
    <div className="st-phone-blank">
      <Icon name="image" size={28} />
    </div>
  );
  const feedRatio = aspect === "1:1" ? "1" : aspect === "9:16" ? "0.5625" : "0.8";
  return (
    <div className="st-phone">
      <div className="st-phone-bar">
        <span>9:41</span>
        <span>●●●</span>
      </div>
      {vertical ? (
        <div className={`st-phone-vertical st-phone-${network}`}>
          {media}
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
          {count > 1 && <span className="st-builder-count">1/{count}</span>}
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
            {media}
            {count > 1 && <span className="st-builder-count">1/{count}</span>}
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

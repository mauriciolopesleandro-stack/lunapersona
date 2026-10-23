import { useMemo, useState } from "react";
import { aspectRatio, ASPECTS, flagProblem, translateFlags, type Aspect, type PostFlags } from "../../api/_flags";
import { Icon } from "./Icon";
import { NETWORKS, Thumb } from "./shared";
import { useStudio, type Item, type Network } from "./store";

// Nova publicacao no fluxo de tres passos (como no Instagram): escolher as
// midias, editar (enquadramento, ordem, capa) e publicar (legenda, opcoes e
// redes). Uma publicacao pode ir para varias redes: o estudio cria uma por rede,
// ligadas pelo mesmo grupo, e as opcoes ("flags") sao traduzidas para cada uma.

const MEDIA_MAX: Record<Network, number> = { instagram: 20, tiktok: 35, telegram: 10 };
const ORDER: Network[] = ["instagram", "tiktok", "telegram"];

interface Target {
  on: boolean;
  formats: string[];
  auto: boolean;
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function localDate(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// Formatos que fazem sentido para as midias escolhidas (o usuario pode mudar).
function suggestedFormats(network: Network, media: Item[]): string[] {
  const videos = media.filter((m) => m.kind === "video").length;
  if (network === "instagram") return media.length === 1 && videos === 1 ? ["Reels"] : ["Feed"];
  if (network === "tiktok") return videos ? ["Vídeo"] : ["Foto"];
  return ["Canal"];
}

// Problemas que impedem salvar para uma rede (vazio = tudo certo).
function problemsFor(network: Network, t: Target, media: Item[], caption: string, flags: PostFlags): string[] {
  const out: string[] = [];
  const net = NETWORKS[network];
  const videos = media.filter((m) => m.kind === "video").length;
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
    if (!video && !photos) out.push("automático: 1 vídeo no destino Vídeo, ou só fotos no destino Foto");
  }
  if (t.auto && network === "instagram") {
    if (t.formats.includes("Reels") && (media.length !== 1 || videos !== 1)) out.push("Reels automático precisa de exatamente 1 vídeo");
    if (t.formats.includes("Stories") && media.length !== 1) out.push("Stories automático precisa de exatamente 1 mídia");
  }
  const issue = flagProblem(network, t.formats, flags);
  if (issue) out.push(issue);
  return out;
}

interface Props {
  defaultDate?: Date | null;
  defaultMedia?: string[];
  onDone?: () => void;
}

export function PostBuilder({ defaultDate, defaultMedia, onDone }: Props) {
  const { items, saveCloudPost, postAction, instagram, tiktok } = useStudio();
  const [step, setStep] = useState<1 | 2 | 3>(defaultMedia?.length ? 2 : 1);
  const [media, setMedia] = useState<string[]>(defaultMedia ?? []);
  const [slide, setSlide] = useState(0);
  const [aspect, setAspect] = useState<Aspect | "original">("original");
  const [cover, setCover] = useState(0);
  const [caption, setCaption] = useState("");
  const [hashtags, setHashtags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [aiGenerated, setAiGenerated] = useState(false);
  const [commentsOff, setCommentsOff] = useState(false);
  const [music, setMusic] = useState("");
  const start = defaultDate ?? new Date(Date.now() + 3600_000);
  const [date, setDate] = useState(localDate(start));
  const [time, setTime] = useState(`${pad(start.getHours())}:${pad(start.getMinutes())}`);
  const [targets, setTargets] = useState<Record<Network, Target>>({
    instagram: { on: true, formats: ["Feed"], auto: false },
    tiktok: { on: false, formats: ["Foto"], auto: false },
    telegram: { on: false, formats: ["Canal"], auto: false },
  });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const byPath = useMemo(() => new Map(items.map((i) => [i.path, i])), [items]);
  const chosen = media.map((p) => byPath.get(p)).filter((i): i is Item => Boolean(i));
  const library = items.filter((i) => i.display !== "arquivado");
  const current = chosen[Math.min(slide, chosen.length - 1)];
  const flags: PostFlags = {
    ...(aiGenerated ? { aiGenerated: true } : {}),
    ...(music.trim() ? { music: music.trim() } : {}),
    ...(commentsOff ? { commentsOff: true } : {}),
    ...(cover > 0 && cover < chosen.length ? { coverIndex: cover } : {}),
    ...(aspect !== "original" ? { aspect } : {}),
  };
  const enabled = ORDER.filter((n) => targets[n].on);
  const problems = Object.fromEntries(ORDER.map((n) => [n, targets[n].on ? problemsFor(n, targets[n], chosen, caption, flags) : []])) as Record<Network, string[]>;
  const blocked = !enabled.length || enabled.some((n) => problems[n].length > 0);

  function toggleMedia(path: string) {
    setMedia((m) => (m.includes(path) ? m.filter((x) => x !== path) : [...m, path]));
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

  function goPublish() {
    // ao chegar no ultimo passo, sugere os formatos pelas midias escolhidas
    setTargets((t) => {
      const next = { ...t };
      for (const n of ORDER) next[n] = { ...t[n], formats: suggestedFormats(n, chosen) };
      return next;
    });
    setStep(3);
  }

  function setTarget(n: Network, patch: Partial<Target>) {
    setTargets((t) => ({ ...t, [n]: { ...t[n], ...patch } }));
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

  async function save(submit: boolean) {
    setMessage(null);
    if (blocked) return setMessage({ ok: false, text: "Corrija os avisos das redes marcadas." });
    const when = date && time ? new Date(`${date}T${time}`).toISOString() : null;
    if (submit && !when) return setMessage({ ok: false, text: "Escolha o dia e a hora." });
    const groupId = enabled.length > 1 ? crypto.randomUUID() : null;
    setSaving(true);
    const done: string[] = [];
    try {
      for (const n of enabled) {
        const t = targets[n];
        const { post } = await saveCloudPost({
          network: n,
          formats: t.formats,
          media,
          caption,
          hashtags,
          scheduledAt: when,
          publishMode: n !== "telegram" && t.auto ? "api" : "manual",
          flags,
          groupId,
        });
        if (submit) await postAction("submit", post.id);
        done.push(NETWORKS[n].label);
      }
      setMessage({
        ok: true,
        text: submit
          ? `Enviada para aprovação (${done.join(", ")}). Aprove em Publicações: um clique aprova todas as redes.`
          : `Rascunho salvo (${done.join(", ")}).`,
      });
      onDone?.();
    } catch (e) {
      const err = e instanceof Error ? e.message : String(e);
      setMessage({ ok: false, text: done.length ? `Salvo em ${done.join(", ")}, mas falhou na próxima rede: ${err}` : err });
    } finally {
      setSaving(false);
    }
  }

  const frame = aspect === "original" ? undefined : String(aspectRatio(aspect));

  return (
    <div className="st-builder">
      <ol className="st-steps-bar" aria-label="Passos">
        {(["Selecionar", "Editar", "Publicar"] as const).map((label, i) => (
          <li key={label} className={step === i + 1 ? "on" : step > i + 1 ? "done" : ""}>
            <span>{i + 1}</span>
            {label}
          </li>
        ))}
      </ol>

      {step === 1 && (
        <>
          {library.length === 0 ? (
            <div className="st-empty">A biblioteca está vazia. Adicione arquivos em Conteúdos.</div>
          ) : (
            <div className="st-picker st-builder-grid">
              {library.map((item) => {
                const n = media.indexOf(item.path);
                return (
                  <div key={item.path} className={n >= 0 ? "st-pick on" : "st-pick"}>
                    <Thumb item={item} onClick={() => toggleMedia(item.path)} />
                    {n >= 0 && <span className="st-pick-n">{n + 1}</span>}
                    <span className="st-pick-name">{item.title}</span>
                  </div>
                );
              })}
            </div>
          )}
          <div className="st-composer-actions">
            <span className="st-hint">{media.length ? `${media.length} selecionada(s), na ordem do carrossel` : "Toque nas mídias na ordem em que devem aparecer."}</span>
            <button type="button" className="st-btn st-btn-primary" disabled={!media.length} onClick={() => setStep(2)}>
              Avançar
              <Icon name="chevronRight" size={16} />
            </button>
          </div>
        </>
      )}

      {step === 2 && (
        <>
          <div className="st-builder-edit">
            <div className="st-builder-stage">
              <div className={frame ? "st-builder-frame cropped" : "st-builder-frame"} style={frame ? { aspectRatio: frame } : undefined}>
                {current &&
                  (current.kind === "image" ? <img src={current.url} alt="" /> : <video src={current.url} controls playsInline />)}
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
            </div>
            <div className="st-builder-side">
              <span className="st-label">Enquadramento das fotos</span>
              <div className="st-seg">
                {(["original", ...ASPECTS] as const).map((a) => (
                  <button key={a} type="button" className={aspect === a ? "active" : ""} onClick={() => setAspect(a)}>
                    {a === "original" ? "Original" : a}
                  </button>
                ))}
              </div>
              <p className="st-hint">
                {aspect === "original"
                  ? "As fotos vão como estão (o Instagram aceita de 4:5 a 1,91:1 no Feed)."
                  : `As fotos serão recortadas no centro em ${aspect} ao enviar. Vídeos não são recortados.`}
              </p>
              <span className="st-label">Ordem e capa</span>
              <ul className="st-order">
                {chosen.map((m, i) => (
                  <li key={m.path} className={i === Math.min(slide, chosen.length - 1) ? "on" : ""}>
                    <button type="button" className="st-order-thumb" onClick={() => setSlide(i)} aria-label={`Ver ${m.title}`}>
                      {m.kind === "image" ? <img src={m.url} alt="" /> : <video src={`${m.url}#t=0.1`} muted preload="metadata" />}
                      <span>{i + 1}</span>
                    </button>
                    <div className="st-order-actions">
                      <button type="button" className={cover === i ? "st-pill on" : "st-pill"} onClick={() => setCover(i)} title="Usar como capa">
                        {cover === i ? "Capa" : "Tornar capa"}
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
            </div>
          </div>
          <div className="st-composer-actions">
            <button type="button" className="st-btn" onClick={() => setStep(1)}>
              <Icon name="chevronLeft" size={16} />
              Voltar
            </button>
            <button type="button" className="st-btn st-btn-primary" disabled={!chosen.length} onClick={goPublish}>
              Avançar
              <Icon name="chevronRight" size={16} />
            </button>
          </div>
        </>
      )}

      {step === 3 && (
        <>
          <div className="st-builder-publish">
            <div className="st-builder-mini" style={frame ? { aspectRatio: frame } : undefined}>
              {chosen[cover] &&
                (chosen[cover].kind === "image" ? <img src={chosen[cover].url} alt="" /> : <video src={`${chosen[cover].url}#t=0.1`} muted preload="metadata" />)}
              {chosen.length > 1 && <span className="st-builder-count">{chosen.length}</span>}
            </div>

            <div className="st-builder-fields">
              <label className="st-label" htmlFor="st-b-caption">Legenda</label>
              <div className="st-textarea">
                <textarea id="st-b-caption" rows={4} value={caption} onChange={(e) => setCaption(e.target.value)} placeholder="Escreva a legenda..." />
                <span className="st-count">{caption.length}</span>
              </div>
              <span className="st-label">Hashtags</span>
              <div className="st-tags">
                {hashtags.map((t) => (
                  <button key={t} type="button" className="st-tag" onClick={() => setHashtags((h) => h.filter((x) => x !== t))} title="Remover">
                    {t}
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

              <span className="st-label">Opções</span>
              <div className="st-flags">
                <label className="st-check">
                  <input type="checkbox" checked={aiGenerated} onChange={(e) => setAiGenerated(e.target.checked)} />
                  Feito com IA
                </label>
                <label className="st-check">
                  <input type="checkbox" checked={commentsOff} onChange={(e) => setCommentsOff(e.target.checked)} />
                  Desligar comentários
                </label>
                <input className="st-input" value={music} onChange={(e) => setMusic(e.target.value)} placeholder="Música sugerida (veja Músicas em alta)" aria-label="Música sugerida" />
              </div>

              <span className="st-label">Redes</span>
              <div className="st-targets">
                {ORDER.map((n) => {
                  const t = targets[n];
                  const net = NETWORKS[n];
                  const connected = n === "instagram" ? instagram?.connected : n === "tiktok" ? tiktok?.connected : false;
                  const translated = t.on ? translateFlags(n, t.formats, t.auto ? "api" : "manual", flags) : [];
                  return (
                    <section key={n} className={`st-target st-target-${n}${t.on ? " on" : ""}`}>
                      <label className="st-target-head">
                        <input type="checkbox" checked={t.on} onChange={(e) => setTarget(n, { on: e.target.checked })} />
                        <Icon name={net.icon} size={16} />
                        <b>{net.label}</b>
                        {t.on && caption.length > net.captionMax && <span className="st-error">legenda {caption.length}/{net.captionMax}</span>}
                      </label>
                      {t.on && (
                        <>
                          <div className="st-row">
                            {net.formats.map((f) => (
                              <label key={f} className="st-check">
                                <input
                                  type="checkbox"
                                  checked={t.formats.includes(f)}
                                  onChange={(e) => setTarget(n, { formats: e.target.checked ? [...t.formats, f] : t.formats.filter((x) => x !== f) })}
                                />
                                {f}
                              </label>
                            ))}
                          </div>
                          {n !== "telegram" && (
                            <label className="st-check">
                              <input type="checkbox" checked={t.auto} onChange={(e) => setTarget(n, { auto: e.target.checked })} />
                              {n === "tiktok" ? "Enviar rascunho automático" : "Publicar automaticamente"}
                              {!connected && t.auto && <span className="st-warn-inline"> · conecte na página {net.label} antes de aprovar</span>}
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

              <div className="st-row">
                <label className="st-label" htmlFor="st-b-date">Quando</label>
                <input id="st-b-date" type="date" className="st-input" value={date} onChange={(e) => setDate(e.target.value)} />
                <input type="time" className="st-input" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Hora" />
              </div>
            </div>
          </div>

          {message && <p className={message.ok ? "st-ok" : "st-error"}>{message.text}</p>}
          <div className="st-composer-actions">
            <button type="button" className="st-btn" onClick={() => setStep(2)} disabled={saving}>
              <Icon name="chevronLeft" size={16} />
              Voltar
            </button>
            <button type="button" className="st-btn" onClick={() => save(false)} disabled={saving || blocked}>
              Salvar rascunho
            </button>
            <button type="button" className="st-btn st-btn-primary" onClick={() => save(true)} disabled={saving || blocked}>
              <Icon name="send" size={16} />
              Enviar para aprovação{enabled.length > 1 ? ` (${enabled.length} redes)` : ""}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

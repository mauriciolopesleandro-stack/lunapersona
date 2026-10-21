import { useMemo, useState } from "react";
import { Icon } from "./Icon";
import { PostFlow } from "./PostFlow";
import { Modal, NETWORKS, Thumb } from "./shared";
import { newPostId, useStudio, type Network, type Post, type PostStatus } from "./store";

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function splitDate(iso: string | null): { date: string; time: string } {
  if (!iso) return { date: "", time: "" };
  const d = new Date(iso);
  return { date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, time: `${pad(d.getHours())}:${pad(d.getMinutes())}` };
}

interface Props {
  initial?: Post | null;
  defaultDate?: Date | null;
  defaultMedia?: string[];
  onDone?: () => void;
}

export function Composer({ initial, defaultDate, defaultMedia, onDone }: Props) {
  const { items, savePost, deletePost, source, posts, saveCloudPost, postAction } = useStudio();
  // No modo banco a publicacao aberta e sempre a versao atual do banco.
  const live = initial && source === "banco" ? posts.find((p) => p.id === initial.id) ?? initial : initial;
  const locked = Boolean(live && ["publicando", "publicado", "cancelado"].includes(live.status));
  const start = splitDate(initial?.scheduledAt ?? (defaultDate ? defaultDate.toISOString() : null));
  const [network, setNetwork] = useState<Network>(initial?.network ?? "instagram");
  const [formats, setFormats] = useState<string[]>(initial?.formats ?? ["Feed"]);
  const [media, setMedia] = useState<string[]>(initial?.media ?? defaultMedia ?? []);
  const [caption, setCaption] = useState(initial?.caption ?? "");
  const [hashtags, setHashtags] = useState<string[]>(initial?.hashtags ?? []);
  const [tagInput, setTagInput] = useState("");
  const [date, setDate] = useState(start.date);
  const [time, setTime] = useState(start.time || "12:00");
  const [slide, setSlide] = useState(0);
  const [picking, setPicking] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const byPath = useMemo(() => new Map(items.map((i) => [i.path, i])), [items]);
  const chosen = media.map((p) => byPath.get(p)).filter((i): i is NonNullable<typeof i> => Boolean(i));
  const net = NETWORKS[network];
  const current = chosen[Math.min(slide, chosen.length - 1)];

  function changeNetwork(n: Network) {
    setNetwork(n);
    setFormats([NETWORKS[n].formats[0]]);
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

  async function save(status: PostStatus) {
    setMessage(null);
    if (status !== "rascunho") {
      if (!chosen.length) return setMessage({ ok: false, text: "Escolha pelo menos uma imagem ou vídeo." });
      if (!date || !time) return setMessage({ ok: false, text: "Escolha o dia e a hora." });
      if (!formats.length) return setMessage({ ok: false, text: "Marque pelo menos um destino." });
      for (const it of chosen) {
        if (it.rating === "adulto" && network !== "telegram") {
          return setMessage({ ok: false, text: `“${it.title}” é conteúdo adulto: só pode ir para o Telegram.` });
        }
        if (it.destinations?.length && !it.destinations.includes(network)) {
          return setMessage({ ok: false, text: `“${it.title}” não está liberado para ${net.label} (veja os destinos no conteúdo).` });
        }
      }
    }
    if (caption.length > net.captionMax) {
      return setMessage({ ok: false, text: `A legenda passou do limite do ${net.label} (${net.captionMax} caracteres).` });
    }
    const when = date && time ? new Date(`${date}T${time}`) : null;
    const now = new Date().toISOString();
    setSaving(true);
    await savePost({
      id: initial?.id ?? newPostId(),
      network,
      formats,
      media,
      caption,
      hashtags,
      scheduledAt: when ? when.toISOString() : null,
      status,
      createdAt: initial?.createdAt ?? now,
      updatedAt: now,
    });
    setSaving(false);
    const late = status === "agendado" && when && when.getTime() < Date.now();
    setMessage({
      ok: true,
      text:
        status === "rascunho"
          ? "Rascunho salvo."
          : late
            ? "Agendado, mas esse horário já passou."
            : `Agendado para ${when!.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}.`,
    });
    if (!initial) {
      setMedia([]);
      setCaption("");
      setHashtags([]);
      setSlide(0);
    }
    onDone?.();
  }

  // Modo banco: salva o rascunho (nova versao se o conteudo mudou) e, se
  // pedido, envia para aprovacao. Aprovar/agendar/confirmar ficam no PostFlow.
  async function saveCloud(submit: boolean) {
    setMessage(null);
    if (caption.length > net.captionMax) {
      return setMessage({ ok: false, text: `A legenda passou do limite do ${net.label} (${net.captionMax} caracteres).` });
    }
    if (submit) {
      if (!chosen.length) return setMessage({ ok: false, text: "Escolha pelo menos uma imagem ou vídeo." });
      if (!formats.length) return setMessage({ ok: false, text: "Marque pelo menos um destino." });
    }
    const when = date && time ? new Date(`${date}T${time}`).toISOString() : null;
    setSaving(true);
    try {
      const { post, needsApproval } = await saveCloudPost({ network, formats, media, caption, hashtags, scheduledAt: when }, live);
      if (submit && (post.status === "rascunho" || post.status === "falhou")) await postAction("submit", post.id);
      setMessage({
        ok: true,
        text: submit
          ? "Enviada para aprovação. Aprove em Publicações (ou abrindo a publicação)."
          : needsApproval
            ? `Salvo como versão ${post.version}: o conteúdo mudou, então precisa ser aprovado de novo.`
            : `Salvo (versão ${post.version}).`,
      });
      if (!live) {
        setMedia([]);
        setCaption("");
        setHashtags([]);
        setSlide(0);
        onDone?.();
      }
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!initial || !window.confirm("Excluir esta publicação? Os arquivos continuam na pasta.")) return;
    await deletePost(initial.id);
    onDone?.();
  }

  return (
    <div className="st-composer">
      <fieldset className="st-fieldset" disabled={locked}>
      <div className="st-tabs st-network-tabs">
        {(Object.keys(NETWORKS) as Network[]).map((n) => (
          <button key={n} type="button" className={network === n ? "active" : ""} onClick={() => changeNetwork(n)}>
            <Icon name={NETWORKS[n].icon} size={16} />
            {NETWORKS[n].label}
          </button>
        ))}
      </div>

      <div className="st-composer-body">
        <div className="st-composer-media">
          {current ? (
            <>
              {current.kind === "image" ? <img src={current.url} alt="" /> : <video src={current.url} controls playsInline />}
              {chosen.length > 1 && (
                <>
                  <button type="button" className="st-car st-car-prev" onClick={() => setSlide((s) => (s - 1 + chosen.length) % chosen.length)} aria-label="Anterior">
                    <Icon name="chevronLeft" size={16} />
                  </button>
                  <button type="button" className="st-car st-car-next" onClick={() => setSlide((s) => (s + 1) % chosen.length)} aria-label="Próxima">
                    <Icon name="chevronRight" size={16} />
                  </button>
                  <div className="st-car-dots">
                    {chosen.map((c, i) => (
                      <span key={c.path} className={i === Math.min(slide, chosen.length - 1) ? "on" : ""} />
                    ))}
                  </div>
                </>
              )}
            </>
          ) : (
            <div className="st-composer-placeholder">
              <Icon name="image" size={28} />
              Nenhuma mídia escolhida
            </div>
          )}
          <button type="button" className="st-btn st-btn-small st-composer-pick" onClick={() => setPicking(true)}>
            {chosen.length ? `Mídias (${chosen.length})` : "Escolher da biblioteca"}
          </button>
        </div>

        <div className="st-composer-fields">
          <label className="st-label" htmlFor="st-caption">Legenda</label>
          <div className="st-textarea">
            <textarea id="st-caption" rows={4} value={caption} onChange={(e) => setCaption(e.target.value)} placeholder="Escreva a legenda..." />
            <span className={caption.length > net.captionMax ? "st-count over" : "st-count"}>
              {caption.length}/{net.captionMax}
            </span>
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

          <div className="st-row">
            <span className="st-label">Destino</span>
            {net.formats.map((f) => (
              <label key={f} className="st-check">
                <input
                  type="checkbox"
                  checked={formats.includes(f)}
                  onChange={(e) => setFormats((cur) => (e.target.checked ? [...cur, f] : cur.filter((x) => x !== f)))}
                />
                {f}
              </label>
            ))}
          </div>

          <div className="st-row">
            <label className="st-label" htmlFor="st-date">Quando</label>
            <input id="st-date" type="date" className="st-input" value={date} onChange={(e) => setDate(e.target.value)} />
            <input type="time" className="st-input" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Hora" />
          </div>
        </div>
      </div>

      </fieldset>

      {message && <p className={message.ok ? "st-ok" : "st-error"}>{message.text}</p>}
      <p className="st-hint">
        {source === "banco"
          ? "Toda publicação passa por aprovação. No horário, o estúdio avisa no Telegram; você publica pelo app da rede e confirma aqui."
          : "Por enquanto o estúdio não publica sozinho: no horário, você publica no app da rede. As integrações oficiais vêm depois."}
      </p>

      {source === "banco" ? (
        <>
          {live && ["aprovado", "agendado"].includes(live.status) && (
            <p className="st-warn">Esta publicação já foi aprovada. Mudar mídia, legenda, hashtags ou destinos cria uma nova versão, que precisa ser aprovada de novo (mudar só o horário não).</p>
          )}
          {!locked && (
            <div className="st-composer-actions">
              <button type="button" className="st-btn" onClick={() => saveCloud(false)} disabled={saving}>
                {live ? "Salvar alterações" : "Salvar rascunho"}
              </button>
              {(!live || live.status === "rascunho" || live.status === "falhou") && (
                <button type="button" className="st-btn st-btn-primary" onClick={() => saveCloud(true)} disabled={saving}>
                  <Icon name="send" size={16} />
                  Enviar para aprovação
                </button>
              )}
            </div>
          )}
          {live && <PostFlow postId={live.id} onClose={() => onDone?.()} />}
        </>
      ) : (
      <div className="st-composer-actions">
        {initial && (
          <button type="button" className="st-btn st-btn-danger" onClick={remove} disabled={saving}>
            Excluir
          </button>
        )}
        {initial?.status === "agendado" && (
          <button type="button" className="st-btn" onClick={() => save("publicado")} disabled={saving}>
            <Icon name="check" size={16} />
            Marcar como publicado
          </button>
        )}
        <button type="button" className="st-btn" onClick={() => save("rascunho")} disabled={saving}>
          Salvar como rascunho
        </button>
        <button type="button" className="st-btn st-btn-primary" onClick={() => save("agendado")} disabled={saving}>
          <Icon name="send" size={16} />
          Programar publicação
        </button>
      </div>
      )}

      {picking && (
        <MediaPicker
          selected={media}
          onClose={() => setPicking(false)}
          onConfirm={(paths) => {
            setMedia(paths);
            setSlide(0);
            setPicking(false);
          }}
        />
      )}
    </div>
  );
}

function MediaPicker({ selected, onClose, onConfirm }: { selected: string[]; onClose: () => void; onConfirm: (paths: string[]) => void }) {
  const { items } = useStudio();
  const [picked, setPicked] = useState<string[]>(selected);
  const available = items.filter((i) => i.display !== "arquivado");
  return (
    <Modal title="Escolher mídias" onClose={onClose} wide>
      {available.length === 0 ? (
        <div className="st-empty">A biblioteca está vazia. Adicione arquivos em Conteúdos.</div>
      ) : (
        <div className="st-picker">
          {available.map((item) => {
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
        <button type="button" className="st-btn" onClick={onClose}>
          Cancelar
        </button>
        <button type="button" className="st-btn st-btn-primary" onClick={() => onConfirm(picked)}>
          Usar {picked.length} {picked.length === 1 ? "mídia" : "mídias"}
        </button>
      </div>
    </Modal>
  );
}

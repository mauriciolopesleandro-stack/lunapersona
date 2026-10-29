import { useState } from "react";

// "Gerar esta foto" na Conversa do Assistente: a IA termina a resposta com
// linhas PROMPT / LEGENDA / HASHTAGS / DESTINO (ver CHAT_NOTE em
// api/_assistant.ts). Cada bloco vira um botao; ao confirmar, a foto entra
// na fila do robo do PC (/api/studio?r=plans, action "quick"), que gera na
// Naiaflow e manda para aprovar no Telegram. Varios destinos marcados: a foto
// sai uma vez so (1 credito) e cada destino ganha a sua publicacao. Carrossel
// (Feed e TikTok): 2 a 10 fotos do mesmo ensaio, 1 credito cada; o Stories leva
// so a primeira.

type Slot = "feed" | "stories" | "tiktok" | "telegram18";
const FEED_PHOTOS = 2;
const SLOT_LABEL: Record<Slot, string> = {
  feed: "Instagram Feed",
  stories: "Instagram Stories",
  tiktok: "TikTok",
  telegram18: "+18 Telegram",
};

export interface PhotoBlock {
  prompt: string;
  caption: string;
  hashtags: string;
  slot: Slot;
}

function slotFrom(text: string): Slot {
  const t = text.toLowerCase();
  if (t.includes("18") || t.includes("telegram")) return "telegram18";
  if (t.includes("stor")) return "stories";
  if (t.includes("tiktok")) return "tiktok";
  return "feed";
}

// Le os blocos "PROMPT: ... LEGENDA: ... HASHTAGS: ... DESTINO: ..." da resposta.
export function parsePhotoBlocks(text: string): PhotoBlock[] {
  const blocks: PhotoBlock[] = [];
  let cur: PhotoBlock | null = null;
  for (const raw of text.split("\n")) {
    const line = raw.replace(/^[\s*>-]+/, "").replace(/\*\*/g, "").trim();
    const m = line.match(/^(PROMPT|LEGENDA|HASHTAGS|DESTINO)\s*:\s*(.*)$/i);
    if (!m) continue;
    const label = m[1].toUpperCase();
    const value = m[2].trim();
    if (label === "PROMPT") {
      cur = { prompt: value, caption: "", hashtags: "", slot: "feed" };
      blocks.push(cur);
    } else if (cur) {
      if (label === "LEGENDA") cur.caption = value;
      else if (label === "HASHTAGS") cur.hashtags = value;
      else cur.slot = slotFrom(value);
    }
  }
  return blocks.filter((b) => b.prompt);
}

// marca/desmarca um destino; o +18 nao se mistura com as redes
function toggle(slots: Slot[], k: Slot): Slot[] {
  if (slots.includes(k)) return slots.filter((s) => s !== k);
  if (k === "telegram18") return ["telegram18"];
  return [...slots.filter((s) => s !== "telegram18"), k];
}

// valor de <input type="datetime-local"> daqui a 2 h, na hora/meia hora seguinte
function defaultWhen(): string {
  const d = new Date(Date.now() + 2 * 3600_000);
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function QuickPhoto({ block }: { block: PhotoBlock }) {
  const [open, setOpen] = useState(false);
  const [slots, setSlots] = useState<Slot[]>([block.slot]);
  // padrao do administrador: Feed sai em carrossel de 2 fotos
  const [photos, setPhotos] = useState(block.slot === "feed" ? FEED_PHOTOS : 1);
  const [when, setWhen] = useState(defaultWhen);
  const [prompt, setPrompt] = useState(block.prompt);
  const [caption, setCaption] = useState(block.caption);
  const [hashtags, setHashtags] = useState(block.hashtags);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function queue() {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/studio?r=plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "quick",
          slots,
          photos: canCarousel ? photos : 1,
          prompt,
          caption,
          hashtags: hashtags.split(/[\s,]+/).filter(Boolean),
          scheduled_at: new Date(when).toISOString(),
        }),
      });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
      setDone(true);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const canCarousel = slots.some((k) => k === "feed" || k === "tiktok") && !slots.includes("telegram18");
  const total = canCarousel ? photos : 1;

  if (done) {
    return (
      <p className="st-ok">
        Na fila do robô ({slots.map((k) => SLOT_LABEL[k]).join(" + ")}{total > 1 ? `, carrossel de ${total} fotos` : ""}). A foto chega no Telegram para você aprovar — o robô do PC precisa estar aberto.
      </p>
    );
  }
  if (!open) {
    return (
      <button type="button" className="st-btn st-btn-small st-btn-primary" onClick={() => setOpen(true)}>
        Gerar esta foto
      </button>
    );
  }
  return (
    <div className="st-quick-photo">
      <div className="st-field">
        <span>Destinos (marque quantos quiser)</span>
        <div className="st-quick-slots" role="group" aria-label="Destinos">
          {(Object.keys(SLOT_LABEL) as Slot[]).map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={slots.includes(k)}
              className={slots.includes(k) ? "on" : ""}
              onClick={() => {
                const next = toggle(slots, k);
                if (k === "feed" && next.includes("feed") && photos < FEED_PHOTOS) setPhotos(FEED_PHOTOS);
                setSlots(next);
              }}
            >
              {slots.includes(k) ? "✓ " : ""}
              {SLOT_LABEL[k]}
            </button>
          ))}
        </div>
      </div>
      {canCarousel && (
        <div className="st-field">
          <span>Fotos {photos > 1 ? "(carrossel)" : ""}</span>
          <div className="st-quick-count">
            <button type="button" aria-label="Menos fotos" disabled={photos <= 1} onClick={() => setPhotos(photos - 1)}>
              −
            </button>
            <b aria-live="polite">{photos}</b>
            <button type="button" aria-label="Mais fotos" disabled={photos >= 10} onClick={() => setPhotos(photos + 1)}>
              +
            </button>
            <small>{photos > 1 ? `${photos} fotos do mesmo ensaio, pose e ângulo diferentes` : "foto única · aumente para fazer carrossel"}</small>
          </div>
        </div>
      )}
      <div className="st-row">
        <label className="st-field">
          <span>Publicar em</span>
          <input type="datetime-local" className="st-input" value={when} onChange={(e) => setWhen(e.target.value)} />
        </label>
      </div>
      <label className="st-field">
        <span>Pedido para a Naiaflow</span>
        <textarea className="st-input" rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      </label>
      <label className="st-field">
        <span>Legenda</span>
        <textarea className="st-input" rows={2} value={caption} onChange={(e) => setCaption(e.target.value)} />
      </label>
      <label className="st-field">
        <span>Hashtags</span>
        <input className="st-input" value={hashtags} onChange={(e) => setHashtags(e.target.value)} />
      </label>
      {slots.includes("telegram18") ? (
        <p className="st-hint">+18: vai só para o canal do Telegram, por isso não junta com Instagram e TikTok.</p>
      ) : (
        <>
          {slots.length > 1 && <p className="st-hint">{total > 1 ? "As mesmas fotos vão" : "A mesma foto vai"} para os {slots.length} destinos, cada um com a sua publicação para aprovar.</p>}
          {total > 1 && slots.includes("stories") && <p className="st-hint">Stories não tem carrossel: vai só a primeira foto.</p>}
        </>
      )}
      {error && <p className="st-error">{error}</p>}
      <div className="st-row">
        <button type="button" className="st-btn st-btn-small st-btn-primary" disabled={busy || !prompt.trim() || !slots.length} onClick={queue}>
          {busy ? "Mandando…" : `Gerar (${total} crédito${total > 1 ? "s" : ""})${slots.length > 1 ? ` · ${slots.length} destinos` : ""}`}
        </button>
        <button type="button" className="st-btn st-btn-small" disabled={busy} onClick={() => setOpen(false)}>
          Cancelar
        </button>
      </div>
    </div>
  );
}

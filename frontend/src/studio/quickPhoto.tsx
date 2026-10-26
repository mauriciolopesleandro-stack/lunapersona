import { useState } from "react";

// "Gerar esta foto" na Conversa do Assistente: a IA termina a resposta com
// linhas PROMPT / LEGENDA / HASHTAGS / DESTINO (ver CHAT_NOTE em
// api/_assistant.ts). Cada bloco vira um botao; ao confirmar, a foto entra
// na fila do robo do PC como um plano de 1 item (/api/studio?r=plans,
// action "quick"), que gera na Naiaflow e manda para aprovar no Telegram.

type Slot = "feed" | "stories" | "tiktok" | "telegram18";
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

// valor de <input type="datetime-local"> daqui a 2 h, na hora/meia hora seguinte
function defaultWhen(): string {
  const d = new Date(Date.now() + 2 * 3600_000);
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function QuickPhoto({ block }: { block: PhotoBlock }) {
  const [open, setOpen] = useState(false);
  const [slot, setSlot] = useState<Slot>(block.slot);
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
          slot,
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

  if (done) {
    return (
      <p className="st-ok">
        Na fila do robô ({SLOT_LABEL[slot]}). A foto chega no Telegram para você aprovar — o robô do PC precisa estar aberto.
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
      <div className="st-row">
        <label className="st-field">
          <span>Destino</span>
          <select className="st-input" value={slot} onChange={(e) => setSlot(e.target.value as Slot)}>
            {(Object.keys(SLOT_LABEL) as Slot[]).map((k) => (
              <option key={k} value={k}>
                {SLOT_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
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
      {slot === "telegram18" && <p className="st-hint">+18: vai só para o canal do Telegram, com postagem manual e lembrete.</p>}
      {error && <p className="st-error">{error}</p>}
      <div className="st-row">
        <button type="button" className="st-btn st-btn-small st-btn-primary" disabled={busy || !prompt.trim()} onClick={queue}>
          {busy ? "Mandando…" : "Gerar (1 crédito)"}
        </button>
        <button type="button" className="st-btn st-btn-small" disabled={busy} onClick={() => setOpen(false)}>
          Cancelar
        </button>
      </div>
    </div>
  );
}

import { useState } from "react";
import { putSignedUpload } from "./cloud";
import { placedText } from "./quickPhoto";

// "Fazer dancinha" na Conversa: com o video (📎) ou o link de uma dancinha
// (TikTok, Instagram...), o robo do PC faz o Copiar movimento da Naiaflow com a
// luna, sem som, na roupa escolhida, e o mesmo video vira Reels e TikTok
// (cada um com a sua publicacao para aprovar no Telegram).

// roupas salvas na galeria da Naiaflow ("Minhas roupas"); o robo procura pelo nome
const OUTFITS = [
  "Biquíni animal print",
  "Biquíni azul dourado",
  "Biquíni branco",
  "Body rendado vinho",
  "Conjunto casual",
  "Conjunto azul marinho",
  "Conjunto cropped jeans",
  "Conjunto biquíni crochê",
  "Conjunto estampado",
];
const KEEP = "";
const OTHER = "__outra__";

// link que da para usar no Copiar movimento (video de rede social)
export const DANCE_LINK = /https:\/\/(?:www\.|vm\.|vt\.|m\.)?(?:tiktok\.com|instagram\.com\/(?:reel|reels|p)\/|youtube\.com\/shorts\/|youtu\.be\/|x\.com\/|twitter\.com\/)\S*/i;

function defaultWhen(): string {
  const d = new Date(Date.now() + 2 * 3600_000);
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function post<T>(body: object): Promise<T> {
  const r = await fetch("/api/studio?r=plans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
  return b as T;
}

export function DanceCard({ link, file }: { link?: string; file?: File }) {
  const [open, setOpen] = useState(false);
  const [outfit, setOutfit] = useState(KEEP);
  const [other, setOther] = useState("");
  const [seconds, setSeconds] = useState(5);
  // som original da dancinha: na aprovacao do Telegram escolhe entre ele e a musica do Instagram
  const [sound, setSound] = useState<"original" | "sem">("original");
  const [slots, setSlots] = useState<string[]>(["reels", "tiktok"]);
  const [when, setWhen] = useState(defaultWhen);
  const [auto, setAuto] = useState(true);
  const [placed, setPlaced] = useState<string[]>([]);
  const [caption, setCaption] = useState("");
  const [hashtags, setHashtags] = useState("#dancinha #trend #reelsbrasil");
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!link && !file) {
    return <p className="st-hint">Para fazer a dancinha com este vídeo, mande o arquivo de novo (o vídeo não fica guardado depois de recarregar a página).</p>;
  }
  const chosen = outfit === OTHER ? other.trim() : outfit;
  const toggle = (k: string) => setSlots(slots.includes(k) ? slots.filter((s) => s !== k) : [...slots, k]);

  async function queue() {
    setError(null);
    try {
      let source: { link?: string; path?: string } = { link };
      if (file) {
        setBusy("Enviando o vídeo…");
        const up = await post<{ path: string; url: string }>({ action: "dance-url", mime_type: file.type || "video/mp4", size_bytes: file.size });
        await putSignedUpload(up.url, file);
        source = { path: up.path };
      }
      setBusy("Mandando para o robô…");
      const res = await post<{ items: { format: string; scheduled_at: string }[] }>({
        action: "dance",
        ...source,
        seconds,
        outfit: chosen,
        sound,
        slots,
        caption,
        hashtags: hashtags.split(/[\s,]+/).filter(Boolean),
        scheduled_at: auto ? "auto" : new Date(when).toISOString(),
      });
      setPlaced(placedText(res.items ?? []));
      setDone(true);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (done) {
    return (
      <p className="st-ok">
        Dancinha na fila do robô ({slots.map((k) => (k === "reels" ? "Reels" : "TikTok")).join(" + ")}
        {chosen ? `, roupa ${chosen}` : ""}){placed.length ? ` · ${placed.join(" · ")}` : ""}. O vídeo chega no Telegram para você aprovar — o robô do PC precisa estar aberto.
      </p>
    );
  }
  if (!open) {
    return (
      <button type="button" className="st-btn st-btn-small st-btn-primary st-dance-open" onClick={() => setOpen(true)}>
        💃 Fazer dancinha (Reels + TikTok)
      </button>
    );
  }
  return (
    <div className="st-quick-photo st-dance">
      <div className="st-field">
        <span>Destinos</span>
        <div className="st-quick-slots" role="group" aria-label="Destinos">
          {(
            [
              ["reels", "Reels"],
              ["tiktok", "TikTok"],
            ] as const
          ).map(([k, label]) => (
            <button key={k} type="button" aria-pressed={slots.includes(k)} className={slots.includes(k) ? "on" : ""} onClick={() => toggle(k)}>
              {slots.includes(k) ? "✓ " : ""}
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="st-row">
        <label className="st-field">
          <span>Roupa da Luna</span>
          <select className="st-input" value={outfit} onChange={(e) => setOutfit(e.target.value)}>
            <option value={KEEP}>Manter a do vídeo</option>
            {OUTFITS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
            <option value={OTHER}>Outra (nome na galeria)…</option>
          </select>
        </label>
        <label className="st-field">
          <span>Duração</span>
          <select className="st-input" value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>
            <option value={5}>5 s (6 créditos)</option>
            <option value={8}>8 s (9 créditos)</option>
            <option value={10}>10 s (11 créditos)</option>
          </select>
        </label>
        <label className="st-field">
          <span>Som</span>
          <select className="st-input" value={sound} onChange={(e) => setSound(e.target.value as "original" | "sem")}>
            <option value="original">Original da dancinha (escolho na aprovação)</option>
            <option value="sem">Sem som (só a música do Instagram)</option>
          </select>
        </label>
      </div>
      {outfit === OTHER && (
        <label className="st-field">
          <span>Nome da roupa na galeria da Naiaflow</span>
          <input className="st-input" value={other} onChange={(e) => setOther(e.target.value)} placeholder="Ex.: Biquíni animal print" />
        </label>
      )}
      <label className="st-field">
        <span>Quando</span>
        <select className="st-input" value={auto ? "auto" : "data"} onChange={(e) => setAuto(e.target.value === "auto")}>
          <option value="auto">Próximo horário vazio de Reels (metas do dia)</option>
          <option value="data">Escolher data e hora</option>
        </select>
      </label>
      {!auto && (
        <label className="st-field">
          <span>Publicar em</span>
          <input type="datetime-local" className="st-input" value={when} onChange={(e) => setWhen(e.target.value)} />
        </label>
      )}
      <label className="st-field">
        <span>Legenda</span>
        <textarea className="st-input" rows={2} value={caption} onChange={(e) => setCaption(e.target.value)} placeholder="Ex.: Sextou pedindo dancinha 💃" />
      </label>
      <label className="st-field">
        <span>Hashtags</span>
        <input className="st-input" value={hashtags} onChange={(e) => setHashtags(e.target.value)} />
      </label>
      <p className="st-hint">
        O robô faz o Copiar movimento sem som (a música do Instagram entra no Reels). Sem trocar a roupa, a Luna sai com a roupa e o cenário do vídeo de
        referência.
      </p>
      {error && <p className="st-error">{error}</p>}
      <div className="st-row">
        <button type="button" className="st-btn st-btn-small st-btn-primary" disabled={busy !== null || !slots.length || (outfit === OTHER && !other.trim())} onClick={queue}>
          {busy ?? `Fazer dancinha (${seconds + 1} créditos)`}
        </button>
        <button type="button" className="st-btn st-btn-small" disabled={busy !== null} onClick={() => setOpen(false)}>
          Cancelar
        </button>
      </div>
    </div>
  );
}

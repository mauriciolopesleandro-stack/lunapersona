import { useEffect, useState } from "react";
import { putSignedUpload } from "./cloud";
import { placedText } from "./quickPhoto";
import { OUTFIT_VIDEO, OutfitPicker, outfitFields, outfitLabel, outfitReady, type OutfitChoice } from "./outfitPicker";
import { DANCE_LINK, OUTFITS } from "../../api/_outfits";

export { DANCE_LINK, OUTFITS };

// "Fazer dancinha" na Conversa: com o video (📎) ou o link de uma dancinha
// (TikTok, Instagram...), o robo do PC faz o Copiar movimento da Naiaflow com a
// luna, sem som, na roupa escolhida, e o mesmo video vira Reels e TikTok
// (cada um com a sua publicacao para aprovar no Telegram).
// 06/11: modo "Trocar pessoa" (padrao, igual ao bot): a cena, a roupa e o fundo
// vem do video e so a pessoa escolhida vira a Luna (ate 12 s).

// quem vira a Luna no Trocar pessoa: o ponto (fracao do quadro) onde o robo toca
const WHO = [
  { key: "meio", label: "A pessoa do meio", point: { x: 0.5, y: 0.45 } },
  { key: "esquerda", label: "A pessoa da esquerda", point: { x: 0.28, y: 0.45 } },
  { key: "direita", label: "A pessoa da direita", point: { x: 0.72, y: 0.45 } },
] as const;


function defaultWhen(): string {
  const d = new Date(Date.now() + 2 * 3600_000);
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// duracao do video (s) lida no navegador; null se nao der para ler
export function videoSeconds(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const v = document.createElement("video");
    const url = URL.createObjectURL(file);
    v.preload = "metadata";
    v.muted = true;
    const end = (n: number | null) => {
      URL.revokeObjectURL(url);
      resolve(n);
    };
    v.onloadedmetadata = () => end(Number.isFinite(v.duration) && v.duration > 0 ? v.duration : null);
    v.onerror = () => end(null);
    v.src = url;
  });
}

// dancinha com o video mandado (31/10): a duracao segue o video - a maior da
// Naiaflow (5, 8 ou 10 s) que cabe nele
export const danceSecondsFor = (video: number, swap = false) => (swap && video >= 12 ? 12 : video >= 10 ? 10 : video >= 8 ? 8 : 5);

async function post<T>(body: object): Promise<T> {
  const r = await fetch("/api/studio?r=plans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
  return b as T;
}

export function DanceCard({ link, file }: { link?: string; file?: File }) {
  const [open, setOpen] = useState(false);
  const [outfit, setOutfit] = useState<OutfitChoice>(OUTFIT_VIDEO);
  const [swap, setSwap] = useState(true);
  const [who, setWho] = useState<(typeof WHO)[number]["key"]>("meio");
  const [seconds, setSeconds] = useState(5);
  // video mandado: a duracao vem dele (o administrador ainda pode trocar)
  const [videoLength, setVideoLength] = useState<number | null>(null);
  useEffect(() => {
    if (!file) return;
    void videoSeconds(file).then((n) => {
      setVideoLength(n);
      if (n) setSeconds(danceSecondsFor(n, true));
    });
  }, [file]);
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
  const chosen = outfitLabel(outfit);
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
      if (!swap && outfit.mode === "foto") setBusy("Enviando a foto da roupa…");
      // Trocar pessoa: a roupa e a do video; vai o ponto de quem vira a Luna
      const roupa = swap ? { mode: "trocar", point: WHO.find((w) => w.key === who)!.point } : await outfitFields(outfit);
      setBusy("Mandando para o robô…");
      const res = await post<{ items: { format: string; scheduled_at: string }[] }>({
        action: "dance",
        ...source,
        seconds,
        ...roupa,
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
        {swap ? ", Trocar pessoa" : chosen ? `, roupa ${chosen}` : ""}){placed.length ? ` · ${placed.join(" · ")}` : ""}. O vídeo chega no Telegram para você aprovar — o robô do PC precisa estar aberto.
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
      <label className="st-field">
        <span>Como fazer</span>
        <select
          className="st-input st-dance-mode"
          value={swap ? "trocar" : "copiar"}
          onChange={(e) => {
            const t = e.target.value === "trocar";
            setSwap(t);
            if (!t && seconds > 10) setSeconds(10);
          }}
        >
          <option value="trocar">🔄 Trocar pessoa (recomendado): a cena fica igual, só a pessoa vira a Luna</option>
          <option value="copiar">💃 Copiar dancinha: a Luna faz o movimento numa cena refeita</option>
        </select>
      </label>
      {swap ? (
        <label className="st-field">
          <span>Quem vira a Luna (se tiver mais de uma pessoa no vídeo)</span>
          <select className="st-input st-dance-who" value={who} onChange={(e) => setWho(e.target.value as (typeof WHO)[number]["key"])}>
            {WHO.map((w) => (
              <option key={w.key} value={w.key}>
                {w.label}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <OutfitPicker value={outfit} onChange={setOutfit} />
      )}
      <div className="st-row">
        <label className="st-field">
          <span>Duração{videoLength ? ` (o vídeo tem ${videoLength.toFixed(1).replace(".", ",")} s)` : ""}</span>
          <select className="st-input" value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>
            <option value={5}>5 s (6 créditos)</option>
            <option value={8}>8 s (9 créditos)</option>
            <option value={10}>10 s (11 créditos)</option>
            {swap && <option value={12}>12 s (13 créditos)</option>}
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
        <span>Legenda (vazia = uma automática do dia, tipo "Sextou pedindo dancinha 💃 Quem dança comigo?")</span>
        <textarea className="st-input" rows={2} value={caption} onChange={(e) => setCaption(e.target.value)} placeholder="Ex.: Sextou pedindo dancinha 💃" />
      </label>
      <label className="st-field">
        <span>Hashtags</span>
        <input className="st-input" value={hashtags} onChange={(e) => setHashtags(e.target.value)} />
      </label>
      {videoLength !== null && videoLength < 5 && <p className="st-warn">O vídeo tem menos de 5 s: a Naiaflow não faz a dancinha. Mande um vídeo de pelo menos 5 s.</p>}
      <p className="st-hint">
        {swap
          ? "O robô faz o Trocar pessoa: o vídeo fica igual (cena, roupa e fundo) e só a pessoa escolhida vira a Luna."
          : "O robô faz o Copiar dancinha com a Luna no lugar da pessoa do vídeo. O cenário sempre vem do vídeo; a roupa, do jeito escolhido acima."}
      </p>
      {error && <p className="st-error">{error}</p>}
      <div className="st-row">
        <button type="button" className="st-btn st-btn-small st-btn-primary" disabled={busy !== null || !slots.length || (!swap && !outfitReady(outfit))} onClick={queue}>
          {busy ?? `Fazer dancinha (${seconds + 1} créditos)`}
        </button>
        <button type="button" className="st-btn st-btn-small" disabled={busy !== null} onClick={() => setOpen(false)}>
          Cancelar
        </button>
      </div>
    </div>
  );
}

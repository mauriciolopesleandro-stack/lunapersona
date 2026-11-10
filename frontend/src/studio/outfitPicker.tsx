import { useEffect, useState } from "react";
import { personaShort } from "./persona";
import { OUTFITS } from "../../api/_outfits";
import { putSignedUpload } from "./cloud";

// Roupa da Luna na dancinha e no video longo (05/11, igual ao bot do Telegram):
// igual ao video (padrao), uma de "Minhas roupas" da Naiaflow (a lista que o robo
// le da galeria) ou a foto de uma roupa (so para este video, sem custo a mais).

export interface OutfitChoice {
  mode: "video" | "galeria" | "foto";
  name: string; // roupa de "Minhas roupas"
  photo: File | null; // foto da roupa
}

export const OUTFIT_VIDEO: OutfitChoice = { mode: "video", name: "", photo: null };

async function post<T>(body: object): Promise<T> {
  const r = await fetch("/api/studio?r=plans", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(b.detail ?? `Erro HTTP ${r.status}`);
  return b as T;
}

// a foto vai como JPEG (o robo guarda como .jpg e manda para a Naiaflow)
async function toJpeg(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 2048 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Não consegui ler a foto da roupa."))), "image/jpeg", 0.92));
}

// campos do pedido (outfit / outfit_ref); sobe a foto da roupa se for o caso
export async function outfitFields(choice: OutfitChoice): Promise<{ outfit?: string; outfit_ref?: string }> {
  if (choice.mode === "galeria" && choice.name) return { outfit: choice.name };
  if (choice.mode === "foto" && choice.photo) {
    const up = await post<{ path: string; url: string }>({ action: "ref-url" });
    await putSignedUpload(up.url, await toJpeg(choice.photo));
    return { outfit_ref: up.path };
  }
  return {};
}

export const outfitReady = (c: OutfitChoice) => (c.mode === "galeria" ? !!c.name : c.mode === "foto" ? !!c.photo : true);

export const outfitLabel = (c: OutfitChoice) => (c.mode === "galeria" ? c.name : c.mode === "foto" ? "da foto" : "");

export function OutfitPicker({ value, onChange }: { value: OutfitChoice; onChange: (c: OutfitChoice) => void }) {
  const [names, setNames] = useState<string[]>(OUTFITS);
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => {
    void post<{ names: string[] }>({ action: "outfits" })
      .then((r) => r.names?.length && setNames(r.names))
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!value.photo) return setPreview(null);
    const url = URL.createObjectURL(value.photo);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [value.photo]);

  const options: [OutfitChoice["mode"], string][] = [
    ["video", "🎥 Igual ao vídeo"],
    ["galeria", `👙 Minhas roupas (${names.length})`],
    ["foto", "📸 Foto da roupa"],
  ];
  return (
    <div className="st-field">
      <span>Roupa da {personaShort()}</span>
      <div className="st-quick-slots" role="group" aria-label={`Roupa da ${personaShort()}`}>
        {options.map(([mode, label]) => (
          <button key={mode} type="button" aria-pressed={value.mode === mode} className={value.mode === mode ? "on" : ""} onClick={() => onChange({ ...value, mode })}>
            {value.mode === mode ? "✓ " : ""}
            {label}
          </button>
        ))}
      </div>
      {value.mode === "galeria" && (
        <select className="st-input" value={value.name} onChange={(e) => onChange({ ...value, name: e.target.value })}>
          <option value="">Escolha a roupa…</option>
          {names.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      )}
      {value.mode === "foto" && (
        <div className="st-row">
          <input className="st-input" type="file" accept="image/*" onChange={(e) => onChange({ ...value, photo: e.target.files?.[0] ?? null })} />
          {preview && <img src={preview} alt="Foto da roupa" style={{ height: 72, borderRadius: 8 }} />}
        </div>
      )}
      <span className="st-hint">
        {value.mode === "video"
          ? "A Luna sai com a roupa e o cenário do vídeo."
          : value.mode === "galeria"
            ? "Roupas salvas na sua galeria da Naiaflow (o robô atualiza a lista)."
            : "A peça sozinha ou alguém vestindo. Vale só para este vídeo, sem custo a mais."}
      </span>
    </div>
  );
}

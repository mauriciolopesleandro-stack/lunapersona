import { useEffect, useState } from "react";
import {
  generateImage,
  uploadGenerationReference,
  type GenerationImage,
  type PersonaSummary,
} from "../api/client";
import { downloadFile } from "../lib/download";

interface Props {
  personas: PersonaSummary[];
  ensureAwake: () => Promise<unknown>;
}

interface PackPhoto {
  id: string;
  file: File;
  previewUrl: string;
  width: number;
  height: number;
  uploaded: string;
  result: GenerationImage | null;
  correction: string;
  status: "waiting" | "running" | "done" | "error";
  error: string | null;
}

const MAX_PHOTOS = 20;
const BASE_PROMPT = "same outfit, pose and expression as the reference photo";

// O pack sai no tamanho da foto original (so a pessoa e redesenhada, num
// recorte ampliado): reduzir tudo para ~1 MP deixava o cenario borrado.
// Teto de ~2,4 MP; multiplo de 16 (o Chroma trabalha em blocos de 16 px).
const MAX_PIXELS = 2_400_000;

function outputSize(width: number, height: number) {
  const scale = Math.min(1, Math.sqrt(MAX_PIXELS / (width * height)));
  return { width: Math.round((width * scale) / 16) * 16, height: Math.round((height * scale) / 16) * 16 };
}

function readSize(file: File): Promise<{ url: string; width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => resolve({ url, width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => reject(new Error(`Não consegui abrir ${file.name}.`));
    img.src = url;
  });
}

function errorText(e: unknown) {
  return String(e instanceof Error ? e.message : e);
}

// Aba Pack com a persona: sobe ate 20 fotos (uma sequencia/ensaio) e em cada
// uma so a pessoa vira a persona - cenario, luz, objetos e ordem ficam iguais.
// Uma foto por vez (a GPU faz uma de cada vez); da para refazer qualquer uma
// com uma correcao e baixar todas numeradas.
export function PackSwapPage({ personas, ensureAwake }: Props) {
  const [personaId, setPersonaId] = useState(personas[0]?.id ?? "");
  const [photos, setPhotos] = useState<PackPhoto[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // "recreate": a foto inteira e redesenhada (sem emendas; o cenario pode mudar
  // um pouco). "swap": so a pessoa e trocada e o resto fica identico.
  const [mode, setMode] = useState<"full" | "swap">("full");

  useEffect(() => {
    if (!personaId && personas[0]) setPersonaId(personas[0].id);
  }, [personas, personaId]);

  const personaName = personas.find((p) => p.id === personaId)?.name ?? "a persona";

  function patch(id: string, change: Partial<PackPhoto>) {
    setPhotos((all) => all.map((p) => (p.id === id ? { ...p, ...change } : p)));
  }

  async function addFiles(files: FileList | null) {
    if (!files) return;
    setError(null);
    const room = MAX_PHOTOS - photos.length;
    const chosen = Array.from(files).slice(0, room);
    if (files.length > room) setError(`O pack vai até ${MAX_PHOTOS} fotos - entraram só as primeiras ${room}.`);
    const added: PackPhoto[] = [];
    for (const file of chosen) {
      try {
        const { url, width, height } = await readSize(file);
        added.push({
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          file,
          previewUrl: url,
          width,
          height,
          uploaded: "",
          result: null,
          correction: "",
          status: "waiting",
          error: null,
        });
      } catch (e) {
        setError(errorText(e));
      }
    }
    setPhotos((all) => [...all, ...added]);
  }

  async function swapOne(photo: PackPhoto, seed?: number) {
    patch(photo.id, { status: "running", error: null });
    try {
      const uploaded = photo.uploaded || (await uploadGenerationReference(photo.file));
      const size = outputSize(photo.width, photo.height);
      const res = await generateImage({
        prompt: photo.correction.trim() ? `${photo.correction.trim()}, ${BASE_PROMPT}` : BASE_PROMPT,
        persona_id: personaId,
        width: size.width,
        height: size.height,
        reference_image: uploaded,
        person_swap: true,
        pack_mode: mode,
        seed,
      });
      patch(photo.id, { uploaded, result: res.images[0] ?? null, status: "done" });
    } catch (e) {
      patch(photo.id, { status: "error", error: errorText(e) });
    }
  }

  async function swapAll() {
    setRunning(true);
    setError(null);
    try {
      await ensureAwake();
      // Uma por vez, na ordem do pack (as ja trocadas ficam como estao).
      for (const photo of photos) {
        if (photo.status === "done") continue;
        await swapOne(photo);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRunning(false);
    }
  }

  async function redo(photo: PackPhoto) {
    setRunning(true);
    try {
      await ensureAwake();
      await swapOne(photo, Math.floor(Math.random() * 2 ** 32));
    } finally {
      setRunning(false);
    }
  }

  const done = photos.filter((p) => p.status === "done").length;
  const pending = photos.filter((p) => p.status !== "done").length;

  return (
    <div className="voice-page pack-swap-page">
      <div className="panel">
        <h2>🖼 Pack com {personaName}</h2>
        <p className="muted small">
          Suba uma sequência de fotos (até {MAX_PHOTOS}). Em cada uma a mulher vira {personaName}: a cena, a pose,
          os objetos, o outro e a ordem das fotos seguem o original. Use fotos suas, de ensaios contratados ou de
          bancos de imagem com licença livre. Cada foto leva uns 5-7 minutos.
        </p>
        <label className="voice-field">
          Modo
          <select value={mode} onChange={(e) => setMode(e.target.value as "full" | "swap")} disabled={running}>
            <option value="full">Trocar a pessoa inteira (rosto e corpo da persona, cenário idêntico)</option>
            <option value="swap">Trocar só o rosto e o cabelo (corpo e roupa ficam os da foto)</option>
          </select>
        </label>
        {personas.length > 1 && (
          <label className="voice-field">
            Persona
            <select value={personaId} onChange={(e) => setPersonaId(e.target.value)} disabled={running}>
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="reference-upload">
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            multiple
            disabled={running || photos.length >= MAX_PHOTOS}
            onChange={(e) => {
              addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          🖼 {photos.length ? `Adicionar fotos (${photos.length}/${MAX_PHOTOS})` : "Escolher fotos do pack"}
        </label>
        <div className="content-action-buttons">
          <button type="button" className="primary" disabled={running || pending === 0} onClick={swapAll}>
            {running ? `Trocando... (${done}/${photos.length})` : `Trocar ${pending} foto(s) por ${personaName}`}
          </button>
          <button
            type="button"
            disabled={done === 0}
            onClick={async () => {
              for (const [i, p] of photos.entries()) {
                if (p.result) await downloadFile(p.result.url, `pack_${String(i + 1).padStart(2, "0")}_${p.result.filename}`);
              }
            }}
          >
            ⬇ Baixar todas ({done})
          </button>
          <button
            type="button"
            disabled={running || photos.length === 0}
            onClick={() => {
              if (window.confirm("Limpar o pack? As fotos já trocadas continuam no servidor, mas saem desta tela.")) {
                photos.forEach((p) => URL.revokeObjectURL(p.previewUrl));
                setPhotos([]);
              }
            }}
          >
            Limpar
          </button>
        </div>
        {running && <p className="muted small">Pode deixar a página aberta - as fotos ficam prontas uma a uma.</p>}
        {error && <p className="error small">{error}</p>}
      </div>

      {photos.length > 0 && (
        <div className="swap-options">
          {photos.map((photo, i) => (
            <div key={photo.id} className="swap-option">
              <div className="swap-option-images">
                <figure>
                  <img src={photo.previewUrl} alt="" />
                  <figcaption>{i + 1}. Original</figcaption>
                </figure>
                <figure>
                  {photo.result ? <img src={photo.result.url} alt="" /> : <div className="swap-placeholder" />}
                  <figcaption>
                    {photo.status === "running"
                      ? "Trocando..."
                      : photo.status === "error"
                      ? "Falhou"
                      : photo.status === "done"
                      ? personaName
                      : "Na fila"}
                  </figcaption>
                </figure>
              </div>
              {photo.error && <p className="error small">{photo.error}</p>}
              <input
                type="text"
                value={photo.correction}
                disabled={running}
                placeholder="Correção (opcional). Ex: vestido vermelho justo, sorrindo"
                onChange={(e) => patch(photo.id, { correction: e.target.value })}
              />
              <div className="content-action-buttons">
                <button type="button" disabled={running} onClick={() => redo(photo)}>
                  {photo.status === "done" ? "Refazer" : "Trocar só esta"}
                </button>
                {photo.result && (
                  <button type="button" onClick={() => downloadFile(photo.result!.url, photo.result!.filename)}>
                    ⬇ Baixar
                  </button>
                )}
                <button
                  type="button"
                  disabled={running}
                  onClick={() => {
                    URL.revokeObjectURL(photo.previewUrl);
                    setPhotos((all) => all.filter((p) => p.id !== photo.id));
                  }}
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

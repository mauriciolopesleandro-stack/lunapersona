import { useEffect, useState } from "react";
import {
  generateImage,
  planStory,
  planStoryFromPhotos,
  uploadGenerationReference,
  type GenerationImage,
  type PersonaSummary,
  type StoryPlan,
} from "../api/client";
import { downloadFile } from "../lib/download";

interface Props {
  personas: PersonaSummary[];
  ensureAwake: () => Promise<unknown>;
}

interface StoryPhoto {
  id: string;
  title: string;
  summary: string;
  prompt: string;
  // foto do pack que originou a cena (so como referencia visual)
  sourceUrl?: string;
  result: GenerationImage | null;
  status: "waiting" | "running" | "done" | "error";
  error: string | null;
}

const FORMATS = [
  { id: "9:16", label: "Vertical 9:16 (stories, reels)", width: 864, height: 1536 },
  { id: "4:5", label: "Vertical 4:5 (feed)", width: 1024, height: 1280 },
  { id: "1:1", label: "Quadrado 1:1", width: 1152, height: 1152 },
  { id: "16:9", label: "Horizontal 16:9", width: 1536, height: 864 },
];

function errorText(e: unknown) {
  return String(e instanceof Error ? e.message : e);
}

// Aba Historia: cola uma historia, o modelo de chat do pod planeja a serie de
// fotos (biblia fixa de lugares, roupas e personagens + um prompt por foto que
// repete essas descricoes - e isso que mantem as fotos coerentes), a pessoa
// revisa os prompts e gera todas com a persona, uma por vez.
export function StoryPage({ personas, ensureAwake }: Props) {
  const [personaId, setPersonaId] = useState(personas[0]?.id ?? "");
  const [story, setStory] = useState("");
  const [count, setCount] = useState(8);
  const [formatId, setFormatId] = useState("9:16");
  const [plan, setPlan] = useState<StoryPlan | null>(null);
  const [photos, setPhotos] = useState<StoryPhoto[]>([]);
  const [planning, setPlanning] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [packFiles, setPackFiles] = useState<File[]>([]);

  useEffect(() => {
    if (!personaId && personas[0]) setPersonaId(personas[0].id);
  }, [personas, personaId]);

  const personaName = personas.find((p) => p.id === personaId)?.name ?? "a persona";
  const format = FORMATS.find((f) => f.id === formatId) ?? FORMATS[0];

  function patch(id: string, change: Partial<StoryPhoto>) {
    setPhotos((all) => all.map((p) => (p.id === id ? { ...p, ...change } : p)));
  }

  async function makePlan() {
    setPlanning(true);
    setError(null);
    try {
      await ensureAwake();
      const result = await planStory(personaId, story, count);
      setPlan(result);
      setPhotos(
        result.scenes.map((s, i) => ({
          id: `${Date.now()}-${i}`,
          title: s.title,
          summary: s.summary,
          prompt: s.prompt,
          result: null,
          status: "waiting",
          error: null,
        }))
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPlanning(false);
    }
  }

  async function makePlanFromPhotos() {
    setPlanning(true);
    setError(null);
    try {
      await ensureAwake();
      const names: string[] = [];
      for (const file of packFiles) names.push(await uploadGenerationReference(file));
      const result = await planStoryFromPhotos(personaId, names);
      setPlan(result);
      setPhotos(
        result.scenes.map((sc, i) => ({
          id: `${Date.now()}-${i}`,
          title: sc.title,
          summary: sc.summary,
          prompt: sc.prompt,
          sourceUrl: packFiles[i] ? URL.createObjectURL(packFiles[i]) : undefined,
          result: null,
          status: "waiting",
          error: null,
        }))
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPlanning(false);
    }
  }

  async function generateOne(photo: StoryPhoto, seed?: number) {
    patch(photo.id, { status: "running", error: null });
    try {
      const res = await generateImage({
        prompt: photo.prompt,
        persona_id: personaId,
        width: format.width,
        height: format.height,
        seed,
      });
      patch(photo.id, { result: res.images[0] ?? null, status: "done" });
    } catch (e) {
      patch(photo.id, { status: "error", error: errorText(e) });
    }
  }

  async function generateAll() {
    setRunning(true);
    setError(null);
    try {
      await ensureAwake();
      // Uma por vez, na ordem da historia (as prontas ficam como estao).
      for (const photo of photos) {
        if (photo.status !== "done") await generateOne(photo);
      }
    } finally {
      setRunning(false);
    }
  }

  async function redo(photo: StoryPhoto) {
    setRunning(true);
    try {
      await ensureAwake();
      await generateOne(photo, Math.floor(Math.random() * 2 ** 32));
    } finally {
      setRunning(false);
    }
  }

  const done = photos.filter((p) => p.status === "done").length;
  const pending = photos.filter((p) => p.status !== "done").length;
  const bible = plan?.bible;

  return (
    <div className="voice-page pack-swap-page">
      <div className="panel">
        <h2>📖 História com {personaName}</h2>
        <p className="muted small">
          Cole uma história (começo, meio e fim) e escolha quantas fotos. A IA monta um roteiro fixo - lugares, roupas
          de cada parte e os outros personagens - e escreve o prompt de cada foto repetindo essas descrições, para a
          série ficar coerente. Revise os prompts e gere. Cada foto leva uns 2-3 minutos.
        </p>
        {personas.length > 1 && (
          <label className="voice-field">
            Persona
            <select value={personaId} onChange={(e) => setPersonaId(e.target.value)} disabled={planning || running}>
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <textarea
          rows={8}
          value={story}
          maxLength={12000}
          disabled={planning || running}
          onChange={(e) => setStory(e.target.value)}
          placeholder="Ex: Sábado de manhã a Luna acorda no apartamento dela em São Paulo, faz café e lê na varanda. À tarde encontra a amiga Bia num café da Vila Madalena, as duas riem e tiram selfie. No fim do dia ela vai sozinha ver o pôr do sol no Mirante 9 de Julho..."
        />
        <div className="char-count">{story.length}/12000</div>
        <p className="muted small">
          Ou suba um pack de fotos (até 20): a IA descreve cada foto e monta a história com uma cena por foto. As
          fotos novas são criadas do zero com {personaName} - nada das fotos originais é reaproveitado.
        </p>
        <div className="content-action-buttons">
          <label className="reference-upload">
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              disabled={planning || running}
              onChange={(e) => {
                setPackFiles(Array.from(e.target.files ?? []).slice(0, 20));
                e.target.value = "";
              }}
            />
            🖼 {packFiles.length ? `${packFiles.length} foto(s) escolhida(s)` : "Escolher pack de fotos"}
          </label>
          <button
            type="button"
            disabled={planning || running || packFiles.length === 0 || !personaId}
            onClick={makePlanFromPhotos}
          >
            {planning ? "Lendo as fotos..." : "Montar história das fotos"}
          </button>
        </div>
        <div className="content-action-buttons">
          <label className="voice-field">
            Fotos
            <select value={count} onChange={(e) => setCount(Number(e.target.value))} disabled={planning || running}>
              {[4, 6, 8, 10, 12, 15, 20].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label className="voice-field">
            Formato
            <select value={formatId} onChange={(e) => setFormatId(e.target.value)} disabled={running}>
              {FORMATS.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="content-action-buttons">
          <button
            type="button"
            className="primary"
            disabled={planning || running || story.trim().length < 20 || !personaId}
            onClick={makePlan}
          >
            {planning ? "Planejando a história... (1-3 min)" : photos.length ? "Planejar de novo" : "Planejar as fotos"}
          </button>
          <button type="button" disabled={running || planning || pending === 0} onClick={generateAll}>
            {running ? `Gerando... (${done}/${photos.length})` : `Gerar ${pending} foto(s)`}
          </button>
          <button
            type="button"
            disabled={done === 0}
            onClick={async () => {
              for (const [i, p] of photos.entries()) {
                if (p.result) await downloadFile(p.result.url, `historia_${String(i + 1).padStart(2, "0")}_${p.result.filename}`);
              }
            }}
          >
            ⬇ Baixar todas ({done})
          </button>
        </div>
        {(planning || running) && <p className="muted small">Pode deixar a página aberta.</p>}
        {error && <p className="error small">{error}</p>}
      </div>

      {bible && (
        <div className="panel">
          <h3>Roteiro fixo da história</h3>
          <p className="muted small">É isto que se repete em todas as fotos. Para mudar algo, edite nos prompts abaixo.</p>
          {!!bible.locations?.length && (
            <p className="small">
              <strong>Lugares:</strong> {bible.locations.join(" · ")}
            </p>
          )}
          {!!bible.outfits?.length && (
            <p className="small">
              <strong>Roupas:</strong> {bible.outfits.join(" · ")}
            </p>
          )}
          {!!bible.characters?.length && (
            <p className="small">
              <strong>Outros personagens:</strong> {bible.characters.join(" · ")}
            </p>
          )}
          {bible.light && (
            <p className="small">
              <strong>Luz:</strong> {bible.light}
            </p>
          )}
          {bible.camera && (
            <p className="small">
              <strong>Estilo:</strong> {bible.camera}
            </p>
          )}
        </div>
      )}

      {photos.length > 0 && (
        <div className="swap-options">
          {photos.map((photo, i) => (
            <div key={photo.id} className="swap-option">
              <p className="small">
                <strong>
                  {i + 1}. {photo.title}
                </strong>
                {photo.summary ? ` - ${photo.summary}` : ""}
              </p>
              {photo.sourceUrl && (
                <figure>
                  <img src={photo.sourceUrl} alt="" />
                  <figcaption>Foto do pack (referência)</figcaption>
                </figure>
              )}
              <figure>
                {photo.result ? <img src={photo.result.url} alt="" /> : <div className="swap-placeholder" />}
                <figcaption>
                  {photo.status === "running"
                    ? "Gerando..."
                    : photo.status === "error"
                    ? "Falhou"
                    : photo.status === "done"
                    ? personaName
                    : "Na fila"}
                </figcaption>
              </figure>
              {photo.error && <p className="error small">{photo.error}</p>}
              <textarea
                rows={4}
                value={photo.prompt}
                disabled={running}
                onChange={(e) => patch(photo.id, { prompt: e.target.value })}
              />
              <div className="content-action-buttons">
                <button type="button" disabled={running} onClick={() => redo(photo)}>
                  {photo.status === "done" ? "Refazer" : "Gerar só esta"}
                </button>
                {photo.result && (
                  <button type="button" onClick={() => downloadFile(photo.result!.url, photo.result!.filename)}>
                    ⬇ Baixar
                  </button>
                )}
                <button
                  type="button"
                  disabled={running}
                  onClick={() => setPhotos((all) => all.filter((p) => p.id !== photo.id))}
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

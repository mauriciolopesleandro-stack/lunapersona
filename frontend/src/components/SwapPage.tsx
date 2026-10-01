import { useEffect, useState } from "react";
import {
  personaInFrame,
  swapKeyframes,
  swapVideo,
  uploadGenerationReference,
  uploadSwapVideo,
  type FrameCheck,
  type GenerationImage,
  type PersonaSummary,
  type SwapKeyframes,
  type VideoResponse,
} from "../api/client";
import { downloadFile } from "../lib/download";
import { loadLastVideo, saveLastVideo } from "../lib/lastResult";
import { SWAP_MAX_SECONDS, swapSecondsFor, videoDuration } from "../lib/videoDuration";

interface Props {
  personas: PersonaSummary[];
  ensureAwake: () => Promise<unknown>;
}

interface Option {
  image: GenerationImage | null;
  check: FrameCheck | null;
  extra: string;
  busy: boolean;
  error: string | null;
}

const LAST_KEY = "trocar-video";

function errorText(e: unknown) {
  return String(e instanceof Error ? e.message : e);
}

// Aba Trocar video, em etapas para nao gastar 30-40 min num video errado:
// 1) o estudio tira os quadros principais do video; 2) gera a persona em cada
// um (mesma roupa, pose e cenario) - da para corrigir e gerar de novo;
// 3) a foto escolhida vira a aparencia da persona na troca do video.
export function SwapPage({ personas, ensureAwake }: Props) {
  const [personaId, setPersonaId] = useState(personas[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [duration, setDuration] = useState(0);
  const [uploaded, setUploaded] = useState("");
  const [frames, setFrames] = useState<SwapKeyframes | null>(null);
  const [options, setOptions] = useState<Option[]>([]);
  const [chosen, setChosen] = useState<GenerationImage | null>(null);
  const [quality, setQuality] = useState<"480p" | "720p">("480p");
  const [detail, setDetail] = useState("");
  const [step, setStep] = useState<"idle" | "analyzing" | "swapping">("idle");
  const [error, setError] = useState<string | null>(null);
  const [video, setVideo] = useState<VideoResponse | null>(() => loadLastVideo(LAST_KEY));

  useEffect(() => {
    if (!personaId && personas[0]) setPersonaId(personas[0].id);
  }, [personas, personaId]);

  const seconds = swapSecondsFor(duration);
  const personaName = personas.find((p) => p.id === personaId)?.name ?? "a persona";

  function pickVideo(picked: File | null) {
    setFile(picked);
    setDuration(0);
    setUploaded("");
    setFrames(null);
    setOptions([]);
    setChosen(null);
    if (picked) videoDuration(picked).then(setDuration);
  }

  async function generateOption(i: number, kf: SwapKeyframes, extra: string) {
    setOptions((all) => all.map((o, j) => (j === i ? { ...o, busy: true, error: null } : o)));
    try {
      const { image, check } = await personaInFrame({
        persona_id: personaId,
        frame: kf.frames[i].filename,
        width: kf.width,
        height: kf.height,
        extra: extra.trim(),
      });
      setOptions((all) => all.map((o, j) => (j === i ? { ...o, image, check, busy: false } : o)));
    } catch (e) {
      setOptions((all) => all.map((o, j) => (j === i ? { ...o, busy: false, error: errorText(e) } : o)));
    }
  }

  async function analyze() {
    if (!file || !personaId) return;
    setStep("analyzing");
    setError(null);
    setChosen(null);
    try {
      await ensureAwake();
      const name = uploaded || (await uploadSwapVideo(file));
      setUploaded(name);
      const kf = await swapKeyframes(name, seconds);
      setFrames(kf);
      setOptions(kf.frames.map(() => ({ image: null, check: null, extra: "", busy: true, error: null })));
      // Uma de cada vez: a GPU faz uma imagem por vez de qualquer jeito.
      for (let i = 0; i < kf.frames.length; i++) await generateOption(i, kf, "");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setStep("idle");
    }
  }

  async function chooseOwnPhoto(photo: File | null) {
    if (!photo) return;
    setError(null);
    try {
      await ensureAwake();
      const name = await uploadGenerationReference(photo);
      setChosen({ filename: name, subfolder: "", type: "input", url: URL.createObjectURL(photo) });
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function swap() {
    if (!file || !chosen) return;
    setStep("swapping");
    setError(null);
    setVideo(null);
    try {
      await ensureAwake();
      const name = uploaded || (await uploadSwapVideo(file));
      setUploaded(name);
      const res = await swapVideo({
        video: name,
        image: chosen.filename,
        image_subfolder: chosen.subfolder,
        image_type: chosen.type === "input" ? "input" : "output",
        persona_id: personaId,
        prompt: detail.trim(),
        quality,
        max_seconds: seconds,
      });
      setVideo(res);
      saveLastVideo(LAST_KEY, res);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setStep("idle");
    }
  }

  const busy = step !== "idle" || options.some((o) => o.busy);
  const minutes = (quality === "720p" ? 25 : 12) * Math.ceil(seconds / 5);
  const clip = video?.videos[0];

  return (
    <div className="voice-page swap-page">
      <div className="panel">
        <h2>🔁 Trocar pessoa em vídeo</h2>
        <p className="muted small">
          A pessoa do vídeo vira {personaName}, com o mesmo movimento, cenário e áudio. Antes do vídeo (que demora),
          você aprova como {personaName} vai ficar. Use vídeos seus ou com autorização; funciona melhor com uma pessoa
          só, de corpo visível.
        </p>
        {personas.length > 1 && (
          <label className="voice-field">
            Persona
            <select value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
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
            accept="video/mp4,video/quicktime,video/webm"
            onChange={(e) => pickVideo(e.target.files?.[0] ?? null)}
          />
          🎞 {file ? file.name : "Escolher vídeo"}
        </label>
        {file && duration > 0 && (
          <p className="muted small">
            {duration > SWAP_MAX_SECONDS
              ? `O vídeo tem ${Math.round(duration)} s: a troca usa os primeiros ${SWAP_MAX_SECONDS} s (máximo).`
              : `Vídeo de ${duration.toFixed(1)} s: a troca usa ele inteiro.`}
          </p>
        )}
        <button type="button" className="primary" onClick={analyze} disabled={busy || !file || !personaId}>
          {step === "analyzing" ? "Analisando o vídeo..." : frames ? "1. Analisar de novo" : "1. Analisar vídeo"}
        </button>
        {step === "analyzing" && (
          <p className="muted small">
            A IA está tirando os quadros principais, criando {personaName} em cada um e conferindo (uns 3-5 min por
            foto).
          </p>
        )}
      </div>

      {frames && (
        <div className="panel">
          <h3>2. Escolha como {personaName} vai ficar</h3>
          <p className="muted small">
            A IA criou {personaName} em cada momento do vídeo e conferiu a roupa (se achou erro, já gerou de novo com
            a correção). Cenário, luz e movimento vêm do próprio vídeo; da foto escolhida vêm o rosto, o cabelo e a
            roupa dela no vídeo todo. Confira e escolha uma; se algo estiver errado, escreva a correção e gere de novo.
            Mãos e dedos a IA não confere tão bem - olhe com atenção.
          </p>
          <div className="swap-options">
            {frames.frames.map((frame, i) => {
              const option = options[i];
              const selected = !!option?.image && chosen?.filename === option.image.filename;
              return (
                <div key={frame.filename} className={selected ? "swap-option selected" : "swap-option"}>
                  <div className="swap-option-images">
                    <figure>
                      <img src={frame.url} alt="" />
                      <figcaption>Vídeo</figcaption>
                    </figure>
                    <figure>
                      {option?.image ? <img src={option.image.url} alt="" /> : <div className="swap-placeholder" />}
                      <figcaption>
                        {option?.busy ? "Gerando..." : option?.error ? "Falhou" : personaName}
                      </figcaption>
                    </figure>
                  </div>
                  {option?.error && <p className="error small">{option.error}</p>}
                  {option?.check && !option.busy && (
                    <p className={option.check.coherent === false ? "swap-check warn" : "swap-check"}>
                      {option.check.coherent === true
                        ? `✓ A IA conferiu: a roupa bate com o vídeo${
                            (option.check.attempts ?? 1) > 1 ? " (corrigiu sozinha 1 vez)" : ""
                          }.`
                        : option.check.coherent === false
                        ? `⚠ A IA ainda vê diferença: ${option.check.problems_pt || "confira a roupa"}`
                        : "A IA não conseguiu conferir esta foto - confira você."}
                    </p>
                  )}
                  <input
                    type="text"
                    value={option?.extra ?? ""}
                    placeholder="Correção (opcional). Ex: biquíni verde de oncinha com correntinha"
                    onChange={(e) =>
                      setOptions((all) => all.map((o, j) => (j === i ? { ...o, extra: e.target.value } : o)))
                    }
                  />
                  <div className="content-action-buttons">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => generateOption(i, frames, option?.extra ?? "")}
                    >
                      Gerar de novo
                    </button>
                    <button
                      type="button"
                      className="primary"
                      disabled={!option?.image || option.busy}
                      onClick={() => option?.image && setChosen(option.image)}
                    >
                      {selected ? "✓ Escolhida" : "Usar esta"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
          <label className="reference-upload">
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => chooseOwnPhoto(e.target.files?.[0] ?? null)} />
            🖼 Ou usar uma foto minha de {personaName}
          </label>
          {chosen?.type === "input" && (
            <div className="voice-current">
              <p className="muted small">Sua foto escolhida:</p>
              <img src={chosen.url} alt="" style={{ maxWidth: 200, borderRadius: 8 }} />
            </div>
          )}
        </div>
      )}

      {frames && (
        <div className="panel animate-panel">
          <h3>3. Trocar no vídeo</h3>
          <input
            type="text"
            value={detail}
            onChange={(e) => setDetail(e.target.value)}
            placeholder="Opcional: detalhe da cena. Ex: ela está sorrindo"
          />
          <div className="animate-options">
            <label>
              Qualidade
              <select value={quality} onChange={(e) => setQuality(e.target.value as "480p" | "720p")}>
                <option value="480p">480p (mais rápido)</option>
                <option value="720p">720p</option>
              </select>
            </label>
          </div>
          <button type="button" className="primary" onClick={swap} disabled={busy || !chosen}>
            {step === "swapping" ? "Trocando..." : chosen ? `Trocar por ${personaName}` : "Escolha uma foto acima"}
          </button>
          {step === "swapping" && <p className="muted small">Leva uns {minutes} minutos. Pode deixar a página aberta.</p>}
        </div>
      )}

      {error && <p className="error">{error}</p>}

      {clip && (
        <div className="panel animate-result">
          <video src={clip.url} controls autoPlay loop playsInline />
          <button type="button" className="result-download" onClick={() => downloadFile(clip.url, clip.filename)}>
            ⬇ Baixar vídeo
          </button>
        </div>
      )}
    </div>
  );
}

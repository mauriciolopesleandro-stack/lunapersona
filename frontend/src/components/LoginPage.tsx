import { useEffect, useRef, useState } from "react";
import { getPodStatus, login, wakePod } from "../api/client";
import "./login.css";

// Elenco mostrado na tela de login. So a Luna existe de verdade hoje - as
// outras sao "em breve" (o usuario ja avisou que vai criar depois), por
// isso ficam com status estatico "EM BREVE" em vez de fingir carregamento.
// Os rostos sao recortes da arte de referencia da tela (public/login/).
const ROSTER: { name: string; real: boolean }[] = [
  { name: "Luna", real: true },
  { name: "Aria", real: false },
  { name: "Nova", real: false },
  { name: "Zoe", real: false },
  { name: "Maya", real: false },
  { name: "Ella", real: false },
  { name: "Sophia", real: false },
  { name: "Valentina", real: false },
];

// A tela e desenhada num palco de largura fixa (as mesmas medidas da arte de
// referencia) e escalada inteira para caber na janela - e o que mantem o
// card e o titulo exatamente em cima das areas limpas do cenario. A escala
// garante que o conteudo (minHeight) caiba; a altura do palco estica para
// preencher a janela, e a sobra embaixo vira chao. Em telas estreitas o
// palco ficaria pequeno demais para ler, entao o CSS troca para um layout
// empilhado (ver @media em login.css) e essas variaveis sao ignoradas.
const STAGE_WIDTH = 1536;
const STAGE_MIN_HEIGHT = 780;
const STAGE_MIN_HEIGHT_NO_GPU = 1000;

function useStageFit(minHeight: number): { scale: number; height: number } {
  const [fit, setFit] = useState({ scale: 1, height: minHeight });
  useEffect(() => {
    function update() {
      const scale = Math.min(window.innerWidth / STAGE_WIDTH, window.innerHeight / minHeight);
      setFit({ scale, height: Math.max(minHeight, window.innerHeight / scale) });
    }
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [minHeight]);
  return fit;
}

// Minutos entre tentativas automaticas de religar quando nao ha GPU livre
// (dentro do teto de preco) em nenhum dos dois volumes.
const RETRY_INTERVAL_S = 5 * 60;
const POLL_INTERVAL_MS = 4_000;
// Um pod novo pode levar alguns minutos ate o backend responder: baixar a
// imagem na maquina, sincronizar o volume e subir Ollama + backend.
const WAKE_TIMEOUT_MS = 10 * 60_000;

type Phase = "login" | "starting" | "no-gpu";

interface Props {
  onReady: () => void;
}

function isNoGpuError(message: string): boolean {
  return /not enough free gpus|no instances|nao ha instancias/i.test(message);
}

export function LoginPage({ onReady }: Props) {
  const [phase, setPhase] = useState<Phase>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [lunaPercent, setLunaPercent] = useState(6);
  const [lunaStatus, setLunaStatus] = useState("AGUARDANDO...");
  const [retrySeconds, setRetrySeconds] = useState(RETRY_INTERVAL_S);
  const [showPassword, setShowPassword] = useState(false);
  const cancelledRef = useRef(false);
  const screenRef = useRef<HTMLDivElement>(null);
  const fit = useStageFit(phase === "no-gpu" ? STAGE_MIN_HEIGHT_NO_GPU : STAGE_MIN_HEIGHT);

  // Paralaxe leve: o cenario acompanha o mouse alguns pixels. Escreve direto
  // nas variaveis CSS para nao re-renderizar o React a cada movimento.
  function handlePointerMove(e: React.PointerEvent) {
    const el = screenRef.current;
    if (!el || e.pointerType !== "mouse") return;
    el.style.setProperty("--lg-px", ((e.clientX / window.innerWidth) * 2 - 1).toFixed(3));
    el.style.setProperty("--lg-py", ((e.clientY / window.innerHeight) * 2 - 1).toFixed(3));
  }

  useEffect(() => {
    // O StrictMode do React (so em dev) monta, desmonta e remonta de
    // proposito pra pegar efeitos sem limpeza - sem resetar aqui, a
    // desmontagem de mentirinha deixava cancelledRef travado em "true"
    // pra sempre, e o polling nunca rodava nem uma vez.
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  // Contagem regressiva so visual enquanto no-gpu; o retry de verdade e
  // disparado pelo effect abaixo quando bate 0.
  useEffect(() => {
    if (phase !== "no-gpu") return;
    setRetrySeconds(RETRY_INTERVAL_S);
    const id = setInterval(() => {
      setRetrySeconds((s) => (s <= 1 ? RETRY_INTERVAL_S : s - 1));
    }, 1000);
    return () => clearInterval(id);
  }, [phase]);

  useEffect(() => {
    if (phase !== "no-gpu" || retrySeconds !== RETRY_INTERVAL_S) return;
    // So dispara de novo apos o primeiro ciclo completo (evita rodar na
    // entrada do estado, que ja chamou startStudio uma vez).
  }, [phase, retrySeconds]);

  async function pollUntilRunning(): Promise<boolean> {
    const deadline = Date.now() + WAKE_TIMEOUT_MS;
    while (Date.now() < deadline && !cancelledRef.current) {
      setLunaPercent((p) => Math.min(92, p + 3));
      try {
        const status = await getPodStatus();
        if (status.running && status.backendReady) return true;
        setLunaStatus(status.running ? "PREPARANDO ESTÚDIO..." : "LIGANDO GPU...");
      } catch {
        // ignora falha isolada de polling e tenta de novo
      }
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    }
    return false;
  }

  async function startStudio() {
    setPhase("starting");
    setLunaStatus("CONECTANDO...");
    setLunaPercent(15);
    try {
      const wake = await wakePod();
      if (!wake.alreadyRunning) {
        setLunaStatus("INICIANDO...");
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (isNoGpuError(message)) {
        setPhase("no-gpu");
        return;
      }
      // Erro de configuracao (ex: RUNPOD_API_KEY ausente) - nao adianta
      // insistir sozinho, deixa entrar mesmo assim (o app mostra o botao
      // "Ligar" e o erro real de novo por la).
      onReady();
      return;
    }

    setLunaStatus("CARREGANDO ESTUDIO...");
    const ok = await pollUntilRunning();
    if (cancelledRef.current) return;
    if (ok) {
      setLunaPercent(100);
      setLunaStatus("ONLINE");
      setTimeout(() => {
        if (!cancelledRef.current) onReady();
      }, 500);
    } else {
      // Demorou demais mas nao veio erro de "sem GPU" - deixa entrar e o
      // app cuida do resto (mesma logica de sempre, botao Ligar visivel).
      onReady();
    }
  }

  // Dispara o retry automatico quando a contagem no banner de "sem GPU"
  // zera (RETRY_INTERVAL_S -> 1 -> volta pra RETRY_INTERVAL_S no proprio
  // set, entao detectamos a virada aqui).
  const prevRetryRef = useRef(retrySeconds);
  useEffect(() => {
    if (phase === "no-gpu" && prevRetryRef.current === 1 && retrySeconds === RETRY_INTERVAL_S) {
      startStudio();
    }
    prevRetryRef.current = retrySeconds;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retrySeconds, phase]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!password.trim() || !username.trim()) return;
    setSubmitting(true);
    setLoginError(null);
    try {
      await login(username, password, remember);
      await startStudio();
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  const minutes = String(Math.floor(retrySeconds / 60)).padStart(2, "0");
  const seconds = String(retrySeconds % 60).padStart(2, "0");

  return (
    <div
      ref={screenRef}
      className={`lg-screen${phase === "no-gpu" ? " lg-screen-no-gpu" : ""}`}
      style={{ "--lg-scale": fit.scale, "--lg-stage-h": `${fit.height}px` } as React.CSSProperties}
      onPointerMove={handlePointerMove}
    >
      <div className="lg-backdrop" aria-hidden="true" />

      <div className="lg-stage">
        <div className="lg-floor" aria-hidden="true" />
        <div className="lg-scene" aria-hidden="true">
          <img className="lg-scene-img" src="/login/scene.webp" alt="" />
          <div className="lg-pod-glow lg-pod-glow-left" />
          <div className="lg-pod-glow lg-pod-glow-right" />
          <img className="lg-char lg-char-left" src="/login/char-left.webp" alt="" />
          <img className="lg-char lg-char-right" src="/login/char-right.webp" alt="" />
          <div className="lg-pod lg-pod-left">
            <div className="lg-pod-scan" />
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <span key={i} className="lg-bubble" />
            ))}
          </div>
          <div className="lg-pod lg-pod-right">
            <div className="lg-pod-scan" />
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <span key={i} className="lg-bubble" />
            ))}
          </div>
        </div>

        <header className="lg-brand">
          <h1>LUNA</h1>
          <p className="lg-brand-sub">AI STUDIO</p>
          <p className="lg-brand-tag">SUAS IAs GANHAM VIDA</p>
        </header>

        {phase === "login" && (
          <form className="lg-card" onSubmit={handleSubmit}>
            <h2>LOGIN</h2>
            <p className="lg-card-sub">ACESSE SEU ESTÚDIO</p>

            <label className="lg-field">
              <span className="lg-sr-only">Usuário ou e-mail</span>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Zm0 2c-4.4 0-8 2.2-8 5v2h16v-2c0-2.8-3.6-5-8-5Z" />
              </svg>
              <input
                type="email"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Usuário ou e-mail"
                autoComplete="username"
                required
              />
            </label>

            <label className="lg-field">
              <span className="lg-sr-only">Senha</span>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M17 9V7A5 5 0 0 0 7 7v2H5v13h14V9h-2ZM9 7a3 3 0 1 1 6 0v2H9V7Zm4 10.7V19h-2v-1.3a2 2 0 1 1 2 0Z" />
              </svg>
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Senha"
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                className="lg-eye"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Esconder senha" : "Mostrar senha"}
                title={showPassword ? "Esconder senha" : "Mostrar senha"}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 5C6.5 5 2.7 9.4 1.5 12c1.2 2.6 5 7 10.5 7s9.3-4.4 10.5-7C21.3 9.4 17.5 5 12 5Zm0 11.5a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9Zm0-7a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z" />
                  {!showPassword && <path d="M3.3 2.3 21.7 20.7l-1.4 1.4L1.9 3.7z" />}
                </svg>
              </button>
            </label>

            <button type="submit" className="lg-submit" disabled={submitting}>
              <span>{submitting ? "ENTRANDO..." : "ENTRAR"}</span>
              <span className="lg-submit-arrows" aria-hidden="true">
                <i>›</i>
                <i>›</i>
                <i>›</i>
              </span>
            </button>

            {loginError && <p className="lg-error">{loginError}</p>}

            <div className="lg-row">
              <label className="lg-remember">
                <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
                Lembrar de mim
              </label>
              <a
                href="#"
                onClick={(e) => e.preventDefault()}
                title="Fale com quem administra o estúdio para trocar a senha"
              >
                Esqueceu a senha?
              </a>
            </div>
          </form>
        )}

        {(phase === "starting" || phase === "no-gpu") && (
          <div className="lg-card lg-card-status">
            <h2>{phase === "no-gpu" ? "ESTÚDIO INDISPONÍVEL" : "INICIANDO..."}</h2>
            <p className="lg-card-sub">
              {phase === "no-gpu"
                ? "A GPU do estúdio está ocupada no momento."
                : "Isso pode levar de 1 a 3 minutos na primeira vez."}
            </p>
            {phase === "starting" && <div className="lg-spinner" aria-hidden="true" />}
          </div>
        )}

        <section className="lg-roster">
          <p className="lg-roster-title">INICIANDO SISTEMA DE IAs...</p>
          <div className="lg-roster-strip">
            {ROSTER.map((p, i) => {
              const isLuna = p.name === "Luna";
              const percent = isLuna ? lunaPercent : 0;
              const online = isLuna && percent >= 100;
              return (
                <div
                  key={p.name}
                  className={`lg-persona${p.real ? "" : " lg-persona-soon"}${online ? " lg-persona-online" : ""}`}
                  style={{ "--lg-i": i, "--lg-pct": `${isLuna ? percent : 0}%` } as React.CSSProperties}
                >
                  <div className="lg-avatar">
                    <img src={`/login/avatar-${p.name.toLowerCase()}.webp`} alt="" />
                  </div>
                  <div className="lg-persona-name">{p.name.toUpperCase()}</div>
                  <div className="lg-persona-pct">{isLuna ? `${Math.round(percent)}%` : "—"}</div>
                  <div className="lg-persona-bar">
                    <div className="lg-persona-bar-fill" />
                  </div>
                  <div className="lg-persona-status">{isLuna ? lunaStatus : "EM BREVE"}</div>
                </div>
              );
            })}
          </div>
        </section>

        {phase === "no-gpu" && (
          <section className="lg-gpu" role="alert">
            <img className="lg-gpu-art" src="/login/gpu-rack.webp" alt="" />
            <div className="lg-gpu-main">
              <strong>NENHUMA GPU DISPONÍVEL NO MOMENTO</strong>
              <p>Todas as IAs estão ocupadas. Tentando novamente em:</p>
              <div className="lg-gpu-timer" aria-label={`${minutes} minutos e ${seconds} segundos`}>
                <div>
                  <b>{minutes}</b>
                  <small>MINUTOS</small>
                </div>
                <b className="lg-gpu-colon">:</b>
                <div>
                  <b>{seconds}</b>
                  <small>SEGUNDOS</small>
                </div>
              </div>
            </div>
            <div className="lg-gpu-side">
              <strong>O QUE ESTÁ ACONTECENDO?</strong>
              <p>
                A RunPod está sem GPU livre para o estúdio agora. Ele tenta de novo sozinho quando o contador
                zerar.
              </p>
              <button type="button" onClick={startStudio}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16Zm1-13h-2v6l5 3 1-1.7-4-2.3V7Z" />
                </svg>
                TENTAR NOVAMENTE AGORA
              </button>
              <button type="button" className="lg-gpu-skip" onClick={onReady}>
                Entrar mesmo assim
              </button>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

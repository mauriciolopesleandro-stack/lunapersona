import { useEffect, useRef, useState } from "react";
import { getPodStatus, login, wakePod } from "../api/client";

// Elenco mostrado na tela de login. So a Luna existe de verdade hoje - as
// outras sao "em breve" (o usuario ja avisou que vai criar depois), por
// isso ficam com status estatico "AGUARDANDO" em vez de fingir carregamento.
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

// Minutos entre tentativas automaticas de religar quando a RunPod responde
// "sem instancias disponiveis" - mesmo ritmo usado manualmente ao longo do
// desenvolvimento, so que agora dentro do proprio app.
const RETRY_INTERVAL_S = 15 * 60;
const POLL_INTERVAL_MS = 4_000;
const WAKE_TIMEOUT_MS = 3 * 60_000;

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
  const cancelledRef = useRef(false);

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
      setLunaPercent((p) => Math.min(92, p + 6));
      setLunaStatus((s) => (s === "CONECTANDO..." ? "INICIANDO..." : s));
      try {
        const status = await getPodStatus();
        if (status.running) return true;
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
    if (!password.trim()) return;
    setSubmitting(true);
    setLoginError(null);
    try {
      await login(password, remember);
      await startStudio();
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-bg">
        <div className="login-glow login-glow-a" />
        <div className="login-glow login-glow-b" />
        <div className="login-grid" />
      </div>

      <div className="login-content">
        <div className="login-brand">
          <span className="login-brand-mark">L</span>
          <div>
            <h1>LUNA AI STUDIO</h1>
            <p>Suas IAs ganham vida</p>
          </div>
        </div>

        {phase === "login" && (
          <form className="login-card" onSubmit={handleSubmit}>
            <h2>Login</h2>
            <p className="muted small">Acesse seu estúdio</p>

            <label className="login-field">
              <span>Usuário ou e-mail</span>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Usuário ou e-mail"
                autoComplete="username"
              />
            </label>

            <label className="login-field">
              <span>Senha</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Senha"
                autoComplete="current-password"
                required
              />
            </label>

            {loginError && <p className="error small">{loginError}</p>}

            <div className="login-row">
              <label className="login-remember">
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

            <button type="submit" className="login-submit" disabled={submitting || !password.trim()}>
              {submitting ? "Entrando..." : "Entrar →"}
            </button>
          </form>
        )}

        {(phase === "starting" || phase === "no-gpu") && (
          <div className="login-card login-status-card">
            <h2>{phase === "no-gpu" ? "Estúdio indisponível" : "Iniciando sistema de IAs..."}</h2>
            <p className="muted small">
              {phase === "no-gpu"
                ? "A GPU do estúdio está ocupada em outro lugar no momento."
                : "Isso pode levar 1 a 3 minutos na primeira vez."}
            </p>
          </div>
        )}

        <div className="login-roster">
          {phase !== "login" && <p className="login-roster-title">Iniciando sistema de IAs...</p>}
          <div className="login-roster-strip">
            {ROSTER.map((p) => {
              const isLuna = p.name === "Luna";
              const percent = isLuna ? lunaPercent : 0;
              const status = isLuna ? lunaStatus : "AGUARDANDO...";
              const online = isLuna && percent >= 100;
              return (
                <div key={p.name} className={`login-roster-item${p.real ? "" : " login-roster-item-locked"}`}>
                  <div className={`login-avatar${online ? " login-avatar-online" : ""}${isLuna ? " login-avatar-luna" : ""}`}>
                    <span>{p.name[0]}</span>
                  </div>
                  <div className="login-roster-name">{p.name.toUpperCase()}</div>
                  <div className="login-roster-bar">
                    <div className="login-roster-bar-fill" style={{ width: `${isLuna ? percent : 4}%` }} />
                  </div>
                  <div className="login-roster-pct">{isLuna ? `${Math.round(percent)}%` : "—"}</div>
                  <div className="login-roster-status">{isLuna ? status : "EM BREVE"}</div>
                </div>
              );
            })}
          </div>
        </div>

        {phase === "no-gpu" && (
          <div className="login-gpu-banner">
            <div className="login-gpu-banner-main">
              <div className="login-gpu-icon">⚠</div>
              <div>
                <strong>NENHUMA GPU DISPONÍVEL NO MOMENTO</strong>
                <p>Todas as IAs estão ocupadas. Tentando novamente em:</p>
                <div className="login-gpu-timer">
                  {String(Math.floor(retrySeconds / 60)).padStart(2, "0")}:
                  {String(retrySeconds % 60).padStart(2, "0")}
                </div>
              </div>
            </div>
            <div className="login-gpu-banner-side">
              <strong>O que está acontecendo?</strong>
              <p>A GPU do estúdio (RunPod) está sendo usada por outra pessoa nesse instante. Isso acontece de vez em quando fora do nosso controle.</p>
              <button type="button" onClick={startStudio}>
                Tentar novamente agora
              </button>
              <button type="button" className="login-gpu-skip" onClick={onReady}>
                Entrar mesmo assim
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

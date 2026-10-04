import { useEffect, useState } from "react";
import type { TelegramStatus } from "../api/client";
import { getTelegram, telegramAction } from "../api/client";

function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

// "Meu perfil" > Avisos no celular: quando uma foto, video ou roteiro termina,
// o estudio manda no Telegram (com a propria foto/video, se ligado). O token
// do bot fica so no servidor do site (api/telegram.ts).
export function TelegramSettings() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    getTelegram()
      .then(setStatus)
      .catch((e) => setError(errorText(e)));
  }, []);

  async function run(action: Parameters<typeof telegramAction>[0], extra: Record<string, unknown> = {}, done = "") {
    setBusy(action);
    setError(null);
    setNotice(null);
    try {
      setStatus(await telegramAction(action, extra));
      if (action === "token") setToken("");
      if (done) setNotice(done);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const botLink = status?.bot ? `https://t.me/${status.bot}` : null;

  return (
    <div className="panel settings-section profile-password">
      <h2>Avisos no celular (Telegram)</h2>
      <p className="muted small">
        Quando uma foto, um vídeo ou um roteiro termina, chega uma mensagem no seu Telegram - você não precisa ficar na
        frente do computador. Tarefas de menos de 20 segundos não avisam.
      </p>

      {status && !status.storeReady && (
        <p className="profile-notice">
          Falta conectar um banco <strong>Upstash Redis</strong> ao projeto na Vercel (aba <strong>Storage</strong>).
        </p>
      )}

      {status?.connected ? (
        <p className="profile-success">
          Conectado{status.bot ? ` ao bot @${status.bot}` : ""}. Os avisos estão ligados.
        </p>
      ) : (
        <ol className="small">
          <li>
            No Telegram, abra o <strong>@BotFather</strong>, mande <code>/newbot</code> e escolha um nome e um usuário
            para o bot. Ele te responde com um <strong>token</strong>.
          </li>
          <li>Cole o token abaixo e salve.</li>
          <li>
            Abra o seu bot{botLink ? (
              <>
                {" "}
                (<a href={botLink} target="_blank" rel="noreferrer">@{status?.bot}</a>)
              </>
            ) : null}{" "}
            e toque em <strong>Começar</strong>.
          </li>
          <li>Volte aqui e toque em <strong>Conectar</strong>.</li>
        </ol>
      )}

      <fieldset disabled={!status?.storeReady || busy !== null}>
        {!status?.connected && (
          <>
            <label htmlFor="tg-token">{status?.tokenSet ? "Trocar o token do bot" : "Token do bot"}</label>
            <input
              id="tg-token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="123456789:ABC..."
              autoComplete="off"
            />
            <div className="content-action-buttons">
              <button type="button" disabled={!token.trim()} onClick={() => run("token", { token: token.trim() }, "Token salvo.")}>
                {busy === "token" ? "Conferindo..." : "Salvar token"}
              </button>
              <button
                type="button"
                className="primary"
                disabled={!status?.tokenSet}
                onClick={() => run("connect", {}, "Conectado! Mandei uma mensagem no seu Telegram.")}
              >
                {busy === "connect" ? "Conectando..." : "Conectar"}
              </button>
            </div>
          </>
        )}

        {status?.connected && (
          <>
            <label className="profile-show">
              <input
                type="checkbox"
                checked={status.media}
                onChange={(e) => run("media", { value: e.target.checked })}
              />
              Mandar a foto/vídeo junto (passa pelos servidores do Telegram; desligado, só o aviso)
            </label>
            <div className="content-action-buttons">
              <button type="button" onClick={() => run("test", {}, "Mensagem de teste enviada.")}>
                {busy === "test" ? "Enviando..." : "Enviar teste"}
              </button>
              <button type="button" onClick={() => run("forget", {}, "Avisos desligados.")}>
                Desligar avisos
              </button>
            </div>
          </>
        )}
      </fieldset>

      {error && <p className="error small">{error}</p>}
      {notice && <p className="profile-success">{notice}</p>}
    </div>
  );
}

import { useEffect, useState } from "react";
import type { InstagramStatus, TikTokStatus } from "./cloud";
import { Icon } from "./Icon";
import { useStudio } from "./store";

function fmt(iso: string | null) {
  return iso ? new Date(iso).toLocaleDateString("pt-BR") : "—";
}

// Instagram: publicacao automatica pela API oficial (conta profissional do
// proprio administrador; Standard Access, sem revisao da Meta).
export function InstagramPage() {
  const { instagram: fromStore, instagramAction } = useStudio();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  // A conexao do Instagram fica no servidor: nao depende da pasta do
  // computador estar aberta (o store so carrega o estado depois da pasta).
  const [fromServer, setFromServer] = useState<InstagramStatus | null>(null);
  const [state, setState] = useState<"carregando" | "sem-banco" | "pronto" | "erro">("carregando");
  useEffect(() => {
    fetch("/api/studio?r=instagram")
      .then(async (r) => {
        if (r.status === 501) return setState("sem-banco");
        if (!r.ok) return setState("erro");
        setFromServer(await r.json());
        setState("pronto");
      })
      .catch(() => setState("erro"));
  }, []);
  const instagram = fromStore ?? fromServer;

  async function act(action: "connect" | "disconnect") {
    setBusy(true);
    setMessage(null);
    try {
      await instagramAction(action, action === "connect" ? token : undefined);
      setToken("");
      setMessage({ ok: true, text: action === "connect" ? "Instagram conectado." : "Instagram desconectado." });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="st-page-head">
        <div>
          <h1>Instagram</h1>
          <p>Publicação automática pela API oficial, na sua conta profissional.</p>
        </div>
      </div>

      <section className="st-panel st-settings">
        <h2>Conexão</h2>
        {state === "carregando" && !instagram ? (
          <p>Carregando...</p>
        ) : state === "sem-banco" ? (
          <p>Primeiro ligue o banco de dados (Supabase): a publicação automática roda nele, mesmo com o computador desligado.</p>
        ) : state === "erro" && !instagram ? (
          <p className="st-error">Não consegui consultar a conexão do Instagram. Recarregue a página.</p>
        ) : instagram?.connected ? (
          <>
            <p>
              <span className="st-badge st-badge-aprovado">Conectado</span> @{instagram.username} · token renovado em {fmt(instagram.refreshedAt)}, vale
              até cerca de {fmt(instagram.expiresAt)} (o estúdio renova sozinho toda semana).
            </p>
            {instagram.lastError && <p className="st-error">Último erro: {instagram.lastError}</p>}
            <button type="button" className="st-btn st-btn-danger" disabled={busy} onClick={() => act("disconnect")}>
              Desconectar
            </button>
          </>
        ) : (
          <>
            <p>
              <span className="st-badge st-badge-rascunho">{instagram?.status === "expirado" ? "Token expirado" : "Não conectado"}</span> Cole aqui o
              token de acesso gerado no painel da Meta (passo a passo abaixo). Ele é guardado cifrado e nunca volta para o navegador.
            </p>
            {instagram && !instagram.encryption && (
              <p className="st-warn">Falta cadastrar TOKEN_ENCRYPTION_KEY na Vercel (só servidor, 32+ caracteres aleatórios) e fazer um novo deploy.</p>
            )}
            <div className="st-row">
              <input
                type="password"
                className="st-input st-token-input"
                placeholder="Token de acesso do Instagram"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                autoComplete="off"
              />
              <button type="button" className="st-btn st-btn-primary" disabled={busy || !token.trim()} onClick={() => act("connect")}>
                Conectar
              </button>
            </div>
          </>
        )}
        {message && <p className={message.ok ? "st-ok" : "st-error"}>{message.text}</p>}
      </section>

      <section className="st-panel st-settings">
        <h2>Como gerar o token (uma vez)</h2>
        <ol className="st-steps">
          <li>Conta do Instagram profissional (Empresa ou Criador de conteúdo).</li>
          <li>
            Em <b>developers.facebook.com</b>, entre e crie um app. No caso de uso, escolha o de gerenciar mensagens e conteúdo no Instagram.
          </li>
          <li>
            No app, abra a configuração da <b>API do Instagram com login do Instagram</b> e, em “Gerar tokens de acesso”, adicione a sua conta e gere o
            token.
          </li>
          <li>Cole o token acima. O app fica em modo de desenvolvimento: como publica só na sua conta, não precisa da revisão da Meta.</li>
        </ol>
        <p className="st-hint">
          Como funciona: ao aprovar um post marcado “Publicar automaticamente”, uma cópia temporária das mídias sobe para um armazenamento privado; no
          horário o Instagram busca por um link que vence, e a cópia é apagada logo depois. Fotos viram JPEG (exigência do Instagram). Limite: 100
          posts pela API a cada 24 h.
        </p>
      </section>
    </div>
  );
}

// Resultado da volta do TikTok (/?tiktok=...#tiktok) em texto para o usuario.
function connectResult(code: string): { ok: boolean; text: string } {
  if (code === "conectado") return { ok: true, text: "TikTok conectado." };
  if (code === "sessao-expirada") return { ok: false, text: "Sua sessão do estúdio expirou no meio do caminho. Entre de novo e conecte outra vez." };
  if (code === "state-invalido") return { ok: false, text: "A autorização demorou mais de 10 minutos ou veio de outra aba. Tente conectar de novo." };
  if (code === "sem-permissao-video-upload") return { ok: false, text: "O TikTok não liberou a permissão de enviar vídeos. Conecte de novo e deixe a permissão marcada." };
  if (code === "nao-configurado") return { ok: false, text: "Faltam as chaves do app do TikTok ou a TOKEN_ENCRYPTION_KEY na Vercel." };
  if (code.startsWith("recusado:")) return { ok: false, text: `Autorização cancelada no TikTok (${code.slice(9)}).` };
  if (code.startsWith("erro:")) return { ok: false, text: `O TikTok respondeu com erro: ${code.slice(5)}` };
  return { ok: false, text: `Não consegui conectar (${code}).` };
}

// TikTok: rascunho automatico pela Content Posting API (escopo video.upload).
// No horario o video chega nas notificacoes do app do TikTok; o criador poe a
// musica e publica. Funciona sem a auditoria do TikTok (a auditoria so e exigida
// para publicar direto, sem passar pelo app).
export function TikTokPage() {
  const { tiktok: fromStore, reloadTiktok, disconnectTiktok } = useStudio();
  const [fromServer, setFromServer] = useState<TikTokStatus | null>(null);
  const [state, setState] = useState<"carregando" | "sem-banco" | "pronto" | "erro">("carregando");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(() => {
    const code = new URLSearchParams(window.location.search).get("tiktok");
    return code ? connectResult(code) : null;
  });

  async function load() {
    try {
      const r = await fetch("/api/studio?r=tiktok");
      if (r.status === 501) return setState("sem-banco");
      if (!r.ok) return setState("erro");
      setFromServer(await r.json());
      setState("pronto");
    } catch {
      setState("erro");
    }
  }

  useEffect(() => {
    // tira o ?tiktok= da barra de endereco (recarregar nao repete a mensagem)
    if (window.location.search.includes("tiktok=")) window.history.replaceState(null, "", "/#tiktok");
    load();
    reloadTiktok();
  }, []);
  const tiktok = fromServer ?? fromStore;

  async function disconnect() {
    setBusy(true);
    setMessage(null);
    try {
      await disconnectTiktok();
      await load();
      setMessage({ ok: true, text: "TikTok desconectado." });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  const ready = tiktok?.appConfigured && tiktok.encryption;

  // Teste: manda uma foto fixa do site (1080x1920) e acompanha o status por 2 min.
  const [test, setTest] = useState<string | null>(null);
  async function testPhoto() {
    setTest("Enviando a foto de teste...");
    try {
      const r = await fetch("/api/studio?r=tiktok", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "test-photo" }) });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) return setTest(`Erro: ${b.detail ?? r.status}`);
      for (let i = 0; i < 24; i++) {
        await new Promise((ok) => setTimeout(ok, 5000));
        const s = await fetch(`/api/studio?r=tiktok&action=status&publish_id=${encodeURIComponent(b.publish_id)}`).then((x) => x.json()).catch(() => ({}));
        setTest(`Status: ${s.status ?? s.detail ?? "?"}${s.fail_reason ? ` (${s.fail_reason})` : ""}`);
        if (s.status && s.status !== "PROCESSING_DOWNLOAD" && s.status !== "PROCESSING_UPLOAD") return;
      }
    } catch (e) {
      setTest(`Erro: ${e instanceof Error ? e.message : e}`);
    }
  }

  return (
    <div>
      <div className="st-page-head">
        <div>
          <h1>TikTok</h1>
          <p>Rascunho automático: no horário o vídeo chega no app do TikTok; você coloca a música em alta e publica.</p>
        </div>
      </div>

      <section className="st-panel st-settings">
        <h2>Conexão</h2>
        {state === "carregando" && !tiktok ? (
          <p>Carregando...</p>
        ) : state === "sem-banco" ? (
          <p>Primeiro ligue o banco de dados (Supabase): o envio automático roda nele, mesmo com o computador desligado.</p>
        ) : state === "erro" && !tiktok ? (
          <p className="st-error">Não consegui consultar a conexão do TikTok. Recarregue a página.</p>
        ) : tiktok?.connected ? (
          <>
            <p>
              <span className="st-badge st-badge-aprovado">Conectado</span> {tiktok.name ?? "Conta do TikTok"} · renovado em {fmt(tiktok.refreshedAt)}.
              A conexão vale até {fmt(tiktok.validUntil)} (o estúdio renova o acesso sozinho a cada envio).
            </p>
            {tiktok.lastError && <p className="st-error">Último erro: {tiktok.lastError}</p>}
            <div className="st-row">
              <button type="button" className="st-btn" disabled={busy || (test !== null && !/^(Status: (SEND|PUBLISH|FAILED)|Erro)/.test(test))} onClick={testPhoto}>
                Testar envio de foto
              </button>
              <button type="button" className="st-btn st-btn-danger" disabled={busy} onClick={disconnect}>
                Desconectar
              </button>
            </div>
            {test && <p className="st-hint">{test}</p>}
          </>
        ) : (
          <>
            <p>
              <span className="st-badge st-badge-rascunho">{tiktok?.status === "expirado" ? "Conexão expirada" : "Não conectado"}</span> O botão abre a
              página oficial do TikTok para você entrar e autorizar. O acesso é guardado cifrado e nunca volta para o navegador.
            </p>
            {tiktok && !tiktok.appConfigured && (
              <p className="st-warn">Falta cadastrar TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET na Vercel (do app criado no TikTok for Developers) e fazer um novo deploy.</p>
            )}
            {tiktok && !tiktok.encryption && <p className="st-warn">Falta cadastrar TOKEN_ENCRYPTION_KEY na Vercel.</p>}
            {ready ? (
              <a className="st-btn st-btn-primary" href="/api/studio?r=tiktok&action=connect">
                <Icon name="tiktok" size={16} />
                Conectar TikTok
              </a>
            ) : (
              <button type="button" className="st-btn st-btn-primary" disabled>
                <Icon name="tiktok" size={16} />
                Conectar TikTok
              </button>
            )}
          </>
        )}
        {message && <p className={message.ok ? "st-ok" : "st-error"}>{message.text}</p>}
      </section>

      <section className="st-panel st-settings">
        <h2>Como funciona</h2>
        <ol className="st-steps">
          <li>
            Prepare a publicação (rede TikTok): destino <b>Vídeo</b> com 1 vídeo MP4 ou MOV, ou destino <b>Foto</b> com até 35 fotos. Marque
            “Enviar rascunho automático”.
          </li>
          <li>Ao aprovar, uma cópia temporária sobe para um armazenamento privado (fotos viram JPEG).</li>
          <li>
            No horário, o estúdio envia para o TikTok (as fotos o TikTok baixa por um link do site que vence) e apaga a cópia. O TikTok avisa no
            app (notificações).
          </li>
          <li>Abra o rascunho, coloque a música (veja Músicas em alta) e publique. Nas fotos a legenda já vai junto; no vídeo, cole a legenda.</li>
          <li>Quando o TikTok confirmar a publicação, o estúdio marca como publicado. Se não confirmar, dá para marcar à mão.</li>
        </ol>
        <p className="st-hint">
          Sem envio automático, continua valendo o modo manual: lembrete no Telegram no horário e “Copiar legenda e hashtags”.
        </p>
      </section>

      {tiktok && !tiktok.connected && (
        <section className="st-panel st-settings">
          <h2>Criar o app no TikTok for Developers (uma vez)</h2>
          <ol className="st-steps">
            <li>
              Em <b>developers.tiktok.com</b>, entre com a conta do TikTok e crie um app (Manage apps).
            </li>
            <li>
              Adicione os produtos <b>Login Kit</b> e <b>Content Posting API</b> (com a permissão <b>video.upload</b>).
            </li>
            <li>
              No Login Kit, cadastre o Redirect URI: <code>{tiktok.redirectUri}</code>
            </li>
            <li>Preencha ícone, descrição, Termos de Uso e Política de Privacidade e envie para revisão.</li>
            <li>Copie o Client key e o Client secret para a Vercel (TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET) e faça um novo deploy.</li>
          </ol>
        </section>
      )}
    </div>
  );
}

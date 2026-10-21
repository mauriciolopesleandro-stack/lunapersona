import { useState } from "react";
import { Icon } from "./Icon";
import { useStudio } from "./store";

function fmt(iso: string | null) {
  return iso ? new Date(iso).toLocaleDateString("pt-BR") : "—";
}

// Instagram: publicacao automatica pela API oficial (conta profissional do
// proprio administrador; Standard Access, sem revisao da Meta).
export function InstagramPage() {
  const { source, instagram, instagramAction } = useStudio();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

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
        {source !== "banco" ? (
          <p>Primeiro ligue o banco de dados (Supabase): a publicação automática roda nele, mesmo com o computador desligado.</p>
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

// TikTok: sem a auditoria do TikTok a API so publica em modo privado; ate la,
// publicacao manual com lembrete.
export function TikTokPage() {
  return (
    <div>
      <div className="st-page-head">
        <div>
          <h1>TikTok</h1>
          <p>Por enquanto, publicação manual com lembrete no horário.</p>
        </div>
      </div>
      <section className="st-panel st-settings">
        <h2>Como postar agora</h2>
        <ol className="st-steps">
          <li>Prepare a publicação (rede TikTok) e aprove.</li>
          <li>No horário, o estúdio avisa no Telegram.</li>
          <li>Abra a publicação, use “Copiar legenda e hashtags”, poste pelo app do TikTok e confirme no estúdio.</li>
        </ol>
      </section>
      <section className="st-panel st-settings">
        <h2>Por que ainda não é automático</h2>
        <p>
          A API oficial do TikTok só publica em modo privado até o app passar pela auditoria deles, e as regras pedem a confirmação de quem posta em
          cada publicação. A auditoria é feita para apps abertos ao público; para uso interno ela pode ser recusada. Quando quiser tentar, o caminho é
          criar um app no TikTok for Developers com o produto Content Posting API e pedir a auditoria.
        </p>
      </section>
    </div>
  );
}

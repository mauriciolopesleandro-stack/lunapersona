import { useEffect, useState } from "react";
import type { Profile } from "../api/client";
import { changePassword, getProfile } from "../api/client";

interface Props {
  onLogout: () => void;
}

export function ProfilePage({ onLogout }: Props) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch((e) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, []);

  const minLength = profile?.minPasswordLength ?? 8;
  const canChange = profile?.canChangePassword ?? false;

  // Mesmas regras do servidor (api/change-password.ts), so para avisar antes
  // de enviar - quem decide continua sendo o servidor.
  let problem: string | null = null;
  if (next && next.length < minLength) problem = `A nova senha precisa ter pelo menos ${minLength} caracteres.`;
  else if (next && current && next === current) problem = "A nova senha precisa ser diferente da atual.";
  else if (confirm && next !== confirm) problem = "A confirmação não bate com a nova senha.";

  const ready = canChange && current && next && confirm && !problem && !saving;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await changePassword(current, next);
      setSaved(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Meu perfil</h1>
        <p>Dados da conta de acesso ao estúdio.</p>
      </div>

      {loadError && <p className="error small">{loadError}</p>}

      <div className="panel settings-section profile-card">
        <div className="profile-avatar" aria-hidden="true">
          {(profile?.email || "?")[0].toUpperCase()}
        </div>
        <div className="profile-id">
          <span className="muted small">E-mail de acesso</span>
          <strong>{profile ? profile.email || "—" : "Carregando..."}</strong>
        </div>
        <button type="button" onClick={onLogout}>
          Sair
        </button>
      </div>

      <form className="panel settings-section profile-password" onSubmit={handleSubmit}>
        <h2>Trocar senha</h2>
        <p className="muted small">
          Depois de trocar, a senha antiga para de funcionar. Esta sessão continua aberta.
        </p>

        {profile && !canChange && (
          <p className="profile-notice">
            A troca de senha pelo app ainda não está ativada. Falta conectar um banco <strong>Upstash Redis</strong>{" "}
            ao projeto na Vercel (aba <strong>Storage</strong>). Depois disso e de um redeploy, este formulário passa a
            funcionar.
          </p>
        )}

        <fieldset disabled={!canChange || saving}>
          <label htmlFor="pw-current">Senha atual</label>
          <input
            id="pw-current"
            type={show ? "text" : "password"}
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            autoComplete="current-password"
          />

          <label htmlFor="pw-next">Nova senha</label>
          <input
            id="pw-next"
            type={show ? "text" : "password"}
            value={next}
            onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password"
            minLength={minLength}
          />

          <label htmlFor="pw-confirm">Confirmar nova senha</label>
          <input
            id="pw-confirm"
            type={show ? "text" : "password"}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
          />

          <label className="profile-show">
            <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} />
            Mostrar senhas
          </label>
        </fieldset>

        {problem && <p className="error small">{problem}</p>}
        {error && <p className="error small">{error}</p>}
        {saved && <p className="profile-success">Senha trocada. Use a nova senha no próximo login.</p>}

        <button type="submit" className="primary" disabled={!ready}>
          {saving ? "Salvando..." : "Salvar nova senha"}
        </button>
      </form>
    </div>
  );
}

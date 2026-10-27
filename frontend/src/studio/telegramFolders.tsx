import { useEffect, useMemo, useState } from "react";
import { putSignedUpload } from "./cloud";
import { Icon } from "./Icon";
import { call, prepare, type Product, type Status } from "./sales";
import { Thumb } from "./shared";
import { useStudio, type Item } from "./store";

// Telegram (pastas): tres pastas no PC, dentro de conteudos/vendas/, que o
// administrador enche ou esvazia a mao. "Atualizar" deixa o Telegram igual as
// pastas:
//   ensaio/     -> conteudo do pacote Ensaio (entra o que e novo, sai o que saiu)
//   album-vip/  -> conteudo do pacote Album VIP sem censura
//   vip/        -> postado no canal VIP; tirou da pasta, o post sai do canal
// Antes de aplicar, a pagina mostra o que vai entrar e o que vai sair.

interface Folder {
  key: "ensaio" | "album" | "vip";
  label: string;
  dir: string; // relativo a conteudos/
  slug?: string; // pacote ligado a pasta (o VIP e o canal)
}

const FOLDERS: Folder[] = [
  { key: "ensaio", label: "Ensaio", dir: "vendas/ensaio/", slug: "ensaio" },
  { key: "album", label: "Álbum VIP sem censura", dir: "vendas/album-vip/", slug: "album-vip" },
  { key: "vip", label: "VIP (canal)", dir: "vendas/vip/" },
];

interface Plan {
  folder: Folder;
  product: Product | null;
  inFolder: Item[];
  add: Item[];
  remove: { media_id: string; label: string }[];
}

function plansFor(status: Status, items: Item[]): Plan[] {
  return FOLDERS.map((folder) => {
    const inFolder = items.filter((i) => i.id && i.path.startsWith(folder.dir));
    const ids = new Set(inFolder.map((i) => i.id));
    const label = (id: string, path: string | null) => (path ? path.split("/").pop()! : `arquivo ${id.slice(0, 8)}`);
    if (folder.key === "vip") {
      const posted = new Set(status.vipPosts.map((p) => p.media_id));
      return {
        folder,
        product: null,
        inFolder,
        add: inFolder.filter((i) => !posted.has(i.id!)),
        remove: status.vipPosts.filter((p) => !ids.has(p.media_id)).map((p) => ({ media_id: p.media_id, label: label(p.media_id, p.path) })),
      };
    }
    const product = status.products.find((p) => p.slug === folder.slug) ?? null;
    const inPack = new Set(product?.media.map((m) => m.media_id) ?? []);
    return {
      folder,
      product,
      inFolder,
      add: inFolder.filter((i) => !inPack.has(i.id!)),
      remove: (product?.media ?? []).filter((m) => !ids.has(m.media_id)).map((m) => ({ media_id: m.media_id, label: label(m.media_id, m.path) })),
    };
  });
}

export function TelegramFoldersPage({ onSales }: { onSales: () => void }) {
  const { items, refresh, state } = useStudio();
  const [status, setStatus] = useState<Status | null>(null);
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    call()
      .then(setStatus)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const plans = useMemo(() => (status ? plansFor(status, items) : []), [status, items]);
  const pending = plans.reduce((n, p) => n + p.add.length + p.remove.length, 0);
  const blocked = (p: Plan) =>
    p.folder.key === "vip" ? (!status?.channel ? "Conecte o canal +18 em Meu perfil → Canal +18." : null) : !status?.adminStarted ? "Abra o bot de vendas e toque em Começar." : null;

  async function reread() {
    setError(null);
    setNotice(null);
    setStep("Lendo as pastas...");
    try {
      await refresh();
      setStatus(await call());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStep(null);
    }
  }

  async function apply() {
    setError(null);
    setNotice(null);
    const warnings: string[] = [];
    let done = 0;
    try {
      for (const plan of plans) {
        if (blocked(plan) || (!plan.add.length && !plan.remove.length)) continue;
        for (const r of plan.remove) {
          setStep(`${plan.folder.label}: tirando ${r.label}...`);
          const res =
            plan.folder.key === "vip"
              ? await call({ action: "vip-unpost", media_id: r.media_id })
              : await call({ action: "media-remove", product_id: plan.product!.id, media_id: r.media_id });
          if (res.warning) warnings.push(res.warning);
          done++;
        }
        for (const [i, item] of plan.add.entries()) {
          setStep(`${plan.folder.label}: ${plan.folder.key === "vip" ? "postando" : "guardando"} ${i + 1} de ${plan.add.length}...`);
          const { blob, mime } = await prepare(item);
          const up =
            plan.folder.key === "vip"
              ? await call({ action: "vip-url", mime_type: mime, size_bytes: blob.size })
              : await call({ action: "media-url", product_id: plan.product!.id, mime_type: mime, size_bytes: blob.size });
          await putSignedUpload(up.url!, blob);
          await call(
            plan.folder.key === "vip"
              ? { action: "vip-post", media_id: item.id, path: up.path }
              : { action: "media-add", product_id: plan.product!.id, media_id: item.id, path: up.path }
          );
          done++;
        }
      }
      setStatus(await call());
      setNotice(`Pronto: ${done} mudança(s) aplicada(s).${warnings.length ? ` ${[...new Set(warnings)].join(" ")}` : ""}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus(await call().catch(() => status));
    } finally {
      setStep(null);
    }
  }

  return (
    <div className="st-sales">
      <div className="st-page-head">
        <div>
          <h1>Telegram (pastas)</h1>
          <p>
            Coloque ou tire arquivos destas pastas do seu PC (dentro de <code>conteudos\vendas\</code>) e clique em <b>Atualizar</b>: os pacotes e o canal VIP
            ficam iguais às pastas. Preços e nomes em{" "}
            <button type="button" className="st-link" onClick={onSales}>
              Vendas
            </button>
            .
          </p>
        </div>
      </div>
      {error && <p className="st-error">{error}</p>}
      {notice && <p className="st-ok">{notice}</p>}
      {state !== "ready" && <p className="st-warn">Abra a pasta do estúdio (luna persona) neste navegador para o estúdio enxergar as pastas.</p>}
      {!status && !error && <div className="st-empty">Carregando...</div>}

      {status && (
        <>
          <div className="st-row">
            <button type="button" className="st-btn" disabled={step !== null} onClick={reread}>
              <Icon name="refresh" size={14} />
              Ler as pastas de novo
            </button>
            <button type="button" className="st-btn st-btn-primary" disabled={step !== null || pending === 0} onClick={apply}>
              {pending ? `Atualizar (${pending} mudança${pending > 1 ? "s" : ""})` : "Tudo atualizado"}
            </button>
            {step && <span className="st-hint">{step}</span>}
          </div>

          <div className="st-sales-products">
            {plans.map((plan) => {
              const why = blocked(plan);
              return (
                <section key={plan.folder.key} className="st-panel st-sales-product">
                  <div className="st-sales-product-head">
                    <h2>{plan.folder.label}</h2>
                    <span className="st-hint">{plan.inFolder.length} na pasta</span>
                  </div>
                  <code className="st-folder-path">conteudos\{plan.folder.dir.replace(/\//g, "\\")}</code>
                  <p className="st-hint">
                    {plan.folder.key === "vip"
                      ? `${status.vipPosts.length} post(s) no canal${status.channel ? ` “${status.channel}”` : ""}.`
                      : `${plan.product?.media.length ?? 0} foto(s)/vídeo(s) no pacote.`}
                  </p>
                  {why && <p className="st-warn">{why}</p>}
                  {plan.inFolder.length === 0 ? (
                    <p className="st-hint">Pasta vazia.</p>
                  ) : (
                    <div className="st-sales-media">
                      {plan.inFolder.map((item) => (
                        <div key={item.path} className={plan.add.includes(item) ? "st-pick st-folder-new" : "st-pick"} title={item.path}>
                          <Thumb item={item} />
                          {plan.add.includes(item) && <span className="st-folder-badge">{plan.folder.key === "vip" ? "vai postar" : "vai entrar"}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                  {plan.remove.length > 0 && (
                    <p className="st-warn">
                      {plan.folder.key === "vip" ? "Vai apagar do canal" : "Vai sair do pacote"}: {plan.remove.map((r) => r.label).join(", ")}
                    </p>
                  )}
                </section>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

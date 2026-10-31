import { useEffect, useMemo, useRef, useState } from "react";
import { putSignedUpload } from "./cloud";
import { Icon } from "./Icon";
import { call, prepare, type Product, type Status } from "./sales";
import { Modal, Thumb } from "./shared";
import { useStudio, type Item } from "./store";

// Telegram: tres pastas no PC, dentro de conteudos/vendas/, controladas daqui
// do estudio (ou a mao, pelo Explorer):
//   ensaio/     -> conteudo do pacote Ensaio
//   album-vip/  -> conteudo do pacote Album VIP sem censura
//   vip/        -> postado no canal VIP; tirou da pasta, o post sai do canal
//   previa/     -> previas das mensagens que chamam o fa para comprar (CTA)
// "Adicionar" copia o arquivo (do computador ou da biblioteca) para a pasta e
// manda para aprovar no Telegram (pedido de 30/10): so entra no pacote, no
// canal ou nas previas quando voce aprova la; recusado, o estudio move o
// arquivo para vendas/_removidos. O "x" tira do pacote/canal na hora (nada e
// apagado). "Enviar para aprovar" manda o que mudou a mao.
// Foto +18 agendada com pacotes entra direto no pacote (api/_jobs.ts) sem passar
// pela pasta; "Atualizar" nao tira essas.

interface Folder {
  key: "ensaio" | "album" | "vip" | "previa";
  label: string;
  dir: string; // relativo a conteudos/
  slug?: string; // pacote ligado a pasta (o VIP e o canal)
}

const FOLDERS: Folder[] = [
  { key: "ensaio", label: "Ensaio", dir: "vendas/ensaio/", slug: "ensaio" },
  { key: "album", label: "Álbum VIP sem censura", dir: "vendas/album-vip/", slug: "album-vip" },
  { key: "vip", label: "VIP (canal)", dir: "vendas/vip/" },
  { key: "previa", label: "Prévias (CTA)", dir: "vendas/previa/" },
];

// o que cada pasta faz no Telegram
const ACTIONS = {
  vip: { url: "vip-url", add: "vip-post", remove: "vip-unpost", adding: "postando" },
  previa: { url: "preview-url", add: "preview-add", remove: "preview-remove", adding: "guardando" },
} as const;
const REMOVED_DIR = "vendas/_removidos";
// previas que o robo do PC ainda vai borrar (worker/borrar.py)
const BLUR_DIR = "vendas/previa-borrar";
const ACCEPT = "image/jpeg,image/png,image/webp,video/mp4";

interface Plan {
  folder: Folder;
  product: Product | null;
  inFolder: Item[];
  add: Item[];
  waiting: Item[]; // mandados para aprovar no Telegram
  remove: { media_id: string; label: string }[];
}

const targetOf = (folder: Folder) => (folder.key === "vip" ? "vip" : folder.key === "previa" ? "previa" : "pack");

function plansFor(status: Status, items: Item[]): Plan[] {
  return FOLDERS.map((folder) => {
    const inFolder = items.filter((i) => i.id && i.path.startsWith(folder.dir));
    const ids = new Set(inFolder.map((i) => i.id));
    const product0 = folder.slug ? status.products.find((p) => p.slug === folder.slug) ?? null : null;
    const pendingIds = new Set(
      (status.folderPending ?? []).filter((p) => p.target === targetOf(folder) && (p.target !== "pack" || p.product_id === product0?.id)).map((p) => p.media_id)
    );
    const waiting = inFolder.filter((i) => pendingIds.has(i.id!));
    const label = (id: string, path: string | null) => (path ? path.split("/").pop()! : `arquivo ${id.slice(0, 8)}`);
    if (folder.key === "vip" || folder.key === "previa") {
      const done = folder.key === "vip" ? status.vipPosts : status.previews;
      const posted = new Set(done.map((p) => p.media_id));
      return {
        folder,
        product: null,
        inFolder,
        add: inFolder.filter((i) => !posted.has(i.id!) && !pendingIds.has(i.id!)),
        waiting,
        remove: done.filter((p) => !ids.has(p.media_id)).map((p) => ({ media_id: p.media_id, label: label(p.media_id, p.path) })),
      };
    }
    const product = status.products.find((p) => p.slug === folder.slug) ?? null;
    const inPack = new Set(product?.media.map((m) => m.media_id) ?? []);
    return {
      folder,
      product,
      inFolder,
      add: inFolder.filter((i) => !inPack.has(i.id!) && !pendingIds.has(i.id!)),
      waiting,
      // so sai do pacote o que veio desta pasta: foto +18 que entrou pelo agendamento
      // (pacotes escolhidos no pedido) mora em conteudos/<mes>/+18 e fica no pacote
      remove: (product?.media ?? [])
        .filter((m) => !ids.has(m.media_id) && (!m.path || m.path.startsWith(folder.dir)))
        .map((m) => ({ media_id: m.media_id, label: label(m.media_id, m.path) })),
    };
  });
}

const nextTick = () => new Promise((r) => setTimeout(r, 0));

export function TelegramFoldersPage({ onSales }: { onSales: () => void }) {
  const { items, refresh, state, addFiles, moveItem } = useStudio();
  const [status, setStatus] = useState<Status | null>(null);
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [picking, setPicking] = useState<Folder | null>(null);
  const [captioning, setCaptioning] = useState<Item | null>(null);
  // previas: o robo do PC borra as partes intimas antes (padrao ligado)
  const [blurPreview, setBlurPreview] = useState(true);
  const waitingBlur = items.filter((i) => i.path.startsWith(`${BLUR_DIR}/`));
  // enquanto o robo borra, rele a pasta a cada 15 s para a versao borrada aparecer sozinha
  useEffect(() => {
    if (!waitingBlur.length) return;
    const t = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(t);
  }, [waitingBlur.length, refresh]);
  const fileInput = useRef<HTMLInputElement>(null);
  const target = useRef<Folder | null>(null);
  // os itens mudam depois de copiar arquivos; o "Atualizar" logo em seguida le daqui
  const itemsRef = useRef(items);
  itemsRef.current = items;

  useEffect(() => {
    call()
      .then(setStatus)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  // recusados no Telegram: saem da pasta (vendas/_removidos) e a lista do servidor e limpa
  const rejectedKey = (status?.folderRejected ?? []).map((r) => r.media_id).join(",");
  useEffect(() => {
    const rejected = status?.folderRejected ?? [];
    if (!rejected.length || state !== "ready") return;
    const gone = items.filter(
      (i) => i.id && rejected.some((r) => r.media_id === i.id && FOLDERS.some((f) => targetOf(f) === r.target && i.path.startsWith(f.dir)))
    );
    void (async () => {
      for (const item of gone) await moveItem(item.path, REMOVED_DIR).catch(() => undefined);
      setStatus(await call({ action: "review-clear", media_ids: rejected.map((r) => r.media_id) }));
      if (gone.length) setNotice(`${gone.length} arquivo(s) recusado(s) no Telegram foram para vendas\\_removidos.`);
    })().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rejectedKey, state]);

  const plans = useMemo(() => (status ? plansFor(status, items) : []), [status, items]);
  const pending = plans.reduce((n, p) => n + p.add.length + p.remove.length, 0);
  const blocked = (f: Folder) =>
    f.key === "vip" ? (!status?.channel ? "Conecte o canal +18 em Meu perfil → Canal +18." : null) : !status?.adminStarted ? "Abra o bot de vendas e toque em Começar." : null;
  const doneHint = (f: Folder, p: Plan) =>
    f.key === "vip"
      ? `${status!.vipPosts.length} post(s) no canal${status!.channel ? ` “${status!.channel}”` : ""}.`
      : f.key === "previa"
        ? `${status!.previews.length} prévia(s) nas mensagens. Use fotos com as partes íntimas cobertas.`
        : `${p.product?.media.length ?? 0} foto(s)/vídeo(s) no pacote.`;
  const busy = step !== null;

  async function run(work: () => Promise<string | void>) {
    setError(null);
    setNotice(null);
    try {
      const done = await work();
      if (done) setNotice(done);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus(await call().catch(() => status));
    } finally {
      setStep(null);
    }
  }

  // aplica o que mudou nas pastas (todas, ou so uma)
  async function applyNow(only?: Folder["key"]): Promise<string> {
    let current = await call();
    const warnings: string[] = [];
    let done = 0;
    let sent = 0;
    for (const plan of plansFor(current, itemsRef.current)) {
      if ((only && plan.folder.key !== only) || blocked(plan.folder) || (!plan.add.length && !plan.remove.length)) continue;
      for (const r of plan.remove) {
        setStep(`${plan.folder.label}: tirando ${r.label}...`);
        const res =
          plan.folder.key === "vip" || plan.folder.key === "previa"
            ? await call({ action: ACTIONS[plan.folder.key].remove, media_id: r.media_id })
            : await call({ action: "media-remove", product_id: plan.product!.id, media_id: r.media_id });
        if (res.warning) warnings.push(res.warning);
        done++;
      }
      for (const [i, item] of plan.add.entries()) {
        const special = plan.folder.key === "vip" || plan.folder.key === "previa" ? ACTIONS[plan.folder.key] : null;
        setStep(`${plan.folder.label}: ${special ? special.adding : "guardando"} ${i + 1} de ${plan.add.length}...`);
        const { blob, mime } = await prepare(item);
        const up = special
          ? await call({ action: special.url, mime_type: mime, size_bytes: blob.size })
          : await call({ action: "media-url", product_id: plan.product!.id, mime_type: mime, size_bytes: blob.size });
        await putSignedUpload(up.url!, blob);
        // nao entra direto: chega no Telegram para aprovar
        await call({ action: "review-add", target: targetOf(plan.folder), product_id: plan.product?.id, media_id: item.id, path: up.path });
        sent++;
      }
    }
    current = await call();
    setStatus(current);
    return [
      sent ? `📁 ${sent} arquivo(s) mandado(s) para você aprovar no Telegram: só entram depois que você aprovar lá.` : "",
      done ? `${done} tirado(s) na hora.` : "",
      warnings.length ? [...new Set(warnings)].join(" ") : "",
    ]
      .filter(Boolean)
      .join(" ") || "Nada para mudar.";
  }

  async function addTo(folder: Folder, files: File[]) {
    // previa com "Borrar partes intimas": vai para vendas/previa-borrar; o robo do PC
    // borra e poe em vendas/previa, e ela aparece aqui como nova para conferir
    if (folder.key === "previa" && blurPreview) {
      await run(async () => {
        setStep(`Copiando ${files.length} arquivo(s) para o robô borrar...`);
        const { added, skipped } = await addFiles(files, BLUR_DIR);
        return [
          added ? `${added} arquivo(s) na fila do robô para borrar. Em 1 a 3 min (vídeo demora mais) a versão borrada chega no Telegram para você aprovar (só entra nas mensagens se aprovar). Se o robô não achar partes íntimas, ela fica só na pasta para você conferir.` : "",
          skipped.length ? `Ignorados (tipo não aceito): ${skipped.join(", ")}` : "",
        ]
          .filter(Boolean)
          .join(" ");
      });
      return;
    }
    await run(async () => {
      setStep(`${folder.label}: copiando ${files.length} arquivo(s) para a pasta...`);
      const { added, skipped } = await addFiles(files, folder.dir.replace(/\/$/, ""));
      await nextTick();
      const done = added && !blocked(folder) ? await applyNow(folder.key) : "";
      return [added ? `${added} arquivo(s) na pasta de ${folder.label}.` : "", done, skipped.length ? `Ignorados (tipo não aceito): ${skipped.join(", ")}` : ""]
        .filter(Boolean)
        .join(" ");
    });
  }

  async function takeOut(folder: Folder, item: Item) {
    const name = item.path.split("/").pop();
    const where = folder.key === "vip" ? "Apagar o post do canal" : `Tirar do pacote ${folder.label}`;
    if (!window.confirm(`${where}: ${name}?\n\nO arquivo não é apagado: vai para a pasta conteudos\\vendas\\_removidos.`)) return;
    await run(async () => {
      setStep(`${folder.label}: tirando ${name}...`);
      const plan = plans.find((p) => p.folder.key === folder.key)!;
      let warning: string | null | undefined = null;
      if (folder.key === "vip") {
        if (status?.vipPosts.some((p) => p.media_id === item.id)) warning = (await call({ action: "vip-unpost", media_id: item.id })).warning;
      } else if (folder.key === "previa") {
        if (status?.previews.some((p) => p.media_id === item.id)) await call({ action: "preview-remove", media_id: item.id });
      } else if (plan.product?.media.some((m) => m.media_id === item.id)) {
        await call({ action: "media-remove", product_id: plan.product.id, media_id: item.id });
      }
      await moveItem(item.path, REMOVED_DIR);
      setStatus(await call());
      return `${name} saiu de ${folder.label} (guardado em vendas\\_removidos).${warning ? ` ${warning}` : ""}`;
    });
  }

  return (
    <div className="st-sales">
      <div className="st-page-head">
        <div>
          <h1>Telegram</h1>
          <p>
            O conteúdo dos pacotes e do canal VIP. Adicione ou tire por aqui; as pastas ficam no seu PC em <code>conteudos\vendas\</code> (atalho “Luna Vox -
            Pastas do Telegram” na Área de Trabalho). Preços e nomes em{" "}
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

      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        accept={ACCEPT}
        onChange={(e) => {
          const files = [...(e.target.files ?? [])];
          e.target.value = "";
          if (files.length && target.current) addTo(target.current, files);
        }}
      />

      {status && (
        <>
          <div className="st-row">
            <button
              type="button"
              className="st-btn"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  setStep("Lendo as pastas...");
                  await refresh();
                  setStatus(await call());
                })
              }
            >
              <Icon name="refresh" size={14} />
              Ler as pastas de novo
            </button>
            <button type="button" className="st-btn st-btn-primary" disabled={busy || pending === 0} onClick={() => run(() => applyNow())}>
              {pending ? `Enviar para aprovar no Telegram (${pending})` : "Tudo atualizado"}
            </button>
            {step && <span className="st-hint">{step}</span>}
          </div>

          <div className="st-sales-products">
            {plans.map((plan) => {
              const why = blocked(plan.folder);
              return (
                <section key={plan.folder.key} className="st-panel st-sales-product">
                  <div className="st-sales-product-head">
                    <h2>{plan.folder.label}</h2>
                    <span className="st-hint">{plan.inFolder.length} na pasta</span>
                  </div>
                  <code className="st-folder-path">conteudos\{plan.folder.dir.replace(/\//g, "\\")}</code>
                  <p className="st-hint">{doneHint(plan.folder, plan)}</p>
                  {why && <p className="st-warn">{why}</p>}
                  {plan.inFolder.length === 0 ? (
                    <p className="st-hint">Pasta vazia.</p>
                  ) : (
                    <div className="st-sales-media">
                      {plan.inFolder.map((item) => (
                        <div key={item.path} className={plan.add.includes(item) ? "st-pick st-folder-new" : "st-pick"} title={item.path}>
                          <Thumb item={item} />
                          {plan.add.includes(item) && <span className="st-folder-badge">{plan.folder.key === "vip" ? "vai postar" : "vai entrar"}</span>}
                          {plan.waiting.includes(item) && <span className="st-folder-badge st-folder-wait">⏳ no Telegram</span>}
                          {plan.folder.key === "previa" && !plan.add.includes(item) && (
                            <button
                              type="button"
                              className={status.previews.find((p) => p.media_id === item.id)?.text ? "st-btn st-btn-small st-caption-btn on" : "st-btn st-btn-small st-caption-btn"}
                              disabled={busy}
                              onClick={() => setCaptioning(item)}
                              title={status.previews.find((p) => p.media_id === item.id)?.text || "Criar a chamada desta prévia com IA"}
                            >
                              {status.previews.find((p) => p.media_id === item.id)?.text ? "💬 Chamada" : "✨ Chamada"}
                            </button>
                          )}
                          <button
                            type="button"
                            className="st-icon-btn st-sales-remove"
                            aria-label={`Tirar ${item.title}`}
                            disabled={busy}
                            onClick={() => takeOut(plan.folder, item)}
                          >
                            <Icon name="close" size={14} />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  {plan.remove.length > 0 && (
                    <p className="st-warn">
                      {plan.folder.key === "vip" ? "Vai apagar do canal" : plan.folder.key === "previa" ? "Vai sair das prévias" : "Vai sair do pacote"}:{" "}
                      {plan.remove.map((r) => r.label).join(", ")}
                    </p>
                  )}
                  {plan.folder.key === "previa" && (
                    <>
                      <label className="st-check">
                        <input type="checkbox" checked={blurPreview} onChange={(e) => setBlurPreview(e.target.checked)} />
                        Borrar as partes íntimas antes (o robô do PC faz sozinho)
                      </label>
                      {waitingBlur.length > 0 && (
                        <p className="st-hint">
                          ⏳ {waitingBlur.length} esperando o robô borrar: {waitingBlur.map((i) => i.path.split("/").pop()).join(", ")} (o robô precisa estar aberto)
                        </p>
                      )}
                    </>
                  )}
                  <div className="st-row st-row-tight">
                    <button
                      type="button"
                      className="st-btn st-btn-small"
                      disabled={busy || state !== "ready"}
                      onClick={() => {
                        target.current = plan.folder;
                        fileInput.current?.click();
                      }}
                    >
                      <Icon name="plus" size={14} />
                      Do computador
                    </button>
                    <button type="button" className="st-btn st-btn-small" disabled={busy || state !== "ready"} onClick={() => setPicking(plan.folder)}>
                      <Icon name="grid" size={14} />
                      Da biblioteca
                    </button>
                  </div>
                </section>
              );
            })}
          </div>
        </>
      )}

      {captioning && status && (
        <CaptionEditor
          item={captioning}
          current={status.previews.find((p) => p.media_id === captioning.id)?.text ?? ""}
          onClose={() => setCaptioning(null)}
          onSaved={(s) => {
            setStatus(s);
            setCaptioning(null);
            setNotice("Chamada da prévia salva: ela vai na mensagem diária quando esta prévia sair.");
          }}
        />
      )}

      {picking && (
        <LibraryPicker
          folder={picking}
          onClose={() => setPicking(null)}
          onPick={async (chosen) => {
            setPicking(null);
            const files = await Promise.all(
              chosen.map(async (item) => {
                const blob = await fetch(item.url).then((r) => r.blob());
                return new File([blob], item.path.split("/").pop()!, { type: blob.type });
              })
            );
            await addTo(picking, files);
          }}
        />
      )}
    </div>
  );
}

// Foto (ou um quadro do video) reduzida para a IA olhar: JPEG de ate 768 px.
async function visionImage(item: Item): Promise<string> {
  const canvas = document.createElement("canvas");
  const draw = (src: CanvasImageSource, w: number, h: number) => {
    const scale = Math.min(1, 768 / Math.max(w, h));
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    canvas.getContext("2d")!.drawImage(src, 0, 0, canvas.width, canvas.height);
  };
  if (item.kind === "video") {
    const video = document.createElement("video");
    video.muted = true;
    video.src = item.url;
    await new Promise<void>((ok, fail) => {
      video.onloadeddata = () => ok();
      video.onerror = () => fail(new Error("Não consegui abrir o vídeo."));
    });
    video.currentTime = Math.min(1, (video.duration || 2) / 2);
    await new Promise<void>((ok) => (video.onseeked = () => ok()));
    draw(video, video.videoWidth, video.videoHeight);
  } else {
    const bitmap = await createImageBitmap(await fetch(item.url).then((r) => r.blob()));
    draw(bitmap, bitmap.width, bitmap.height);
  }
  return canvas.toDataURL("image/jpeg", 0.85);
}

// Chamada da previa: a IA olha a foto e sugere 3; o administrador escolhe ou escreve.
function CaptionEditor({ item, current, onClose, onSaved }: { item: Item; current: string; onClose: () => void; onSaved: (s: Status) => void }) {
  const [text, setText] = useState(current);
  const [ideas, setIdeas] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function suggest() {
    setBusy("Olhando a prévia...");
    setError(null);
    try {
      const res = (await call({ action: "preview-suggest", image: await visionImage(item) })) as unknown as { suggestions: string[] };
      setIdeas(res.suggestions);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function save(value: string) {
    setBusy("Salvando...");
    setError(null);
    try {
      onSaved(await call({ action: "preview-text", media_id: item.id, text: value }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }

  return (
    <Modal title="Chamada desta prévia" onClose={onClose} wide>
      <div className="st-caption-editor">
        <div className="st-caption-thumb">
          <Thumb item={item} />
        </div>
        <div>
          <p className="st-hint">
            A IA olha a prévia e escreve chamadas ligadas a ela. <b>{"{nome}"}</b> vira o nome do fã. Quando esta prévia sair na mensagem diária, vai esta
            chamada no lugar da frase do dia.
          </p>
          <button type="button" className="st-btn st-btn-small" disabled={busy !== null} onClick={suggest}>
            ✨ {ideas.length ? "Sugerir outras" : "Sugerir 3 chamadas com IA"}
          </button>
          {ideas.length > 0 && (
            <ul className="st-caption-ideas">
              {ideas.map((idea) => (
                <li key={idea}>
                  <button type="button" className={idea === text ? "on" : ""} onClick={() => setText(idea)}>
                    {idea}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <label className="st-field">
            <span>Chamada</span>
            <textarea className="st-input" rows={3} maxLength={900} value={text} onChange={(e) => setText(e.target.value)} placeholder="{nome}, ..." />
          </label>
          {error && <p className="st-error">{error}</p>}
          <div className="st-composer-actions">
            <span className="st-hint">{busy ?? ""}</span>
            {current && (
              <button type="button" className="st-btn" disabled={busy !== null} onClick={() => save("")}>
                Tirar chamada
              </button>
            )}
            <button type="button" className="st-btn st-btn-primary" disabled={busy !== null || !text.trim() || text.trim() === current} onClick={() => save(text)}>
              Salvar chamada
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

// Escolher da biblioteca (fora das pastas de vendas): o arquivo e copiado para a
// pasta; o original continua onde estava.
function LibraryPicker({ folder, onClose, onPick }: { folder: Folder; onClose: () => void; onPick: (items: Item[]) => void }) {
  const { items } = useStudio();
  const [onlyAdult, setOnlyAdult] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const library = items.filter((i) => !i.path.startsWith("vendas/") && i.display !== "arquivado" && (!onlyAdult || i.rating !== "livre"));
  return (
    <Modal title={`Adicionar em ${folder.label}`} onClose={onClose} wide>
      <label className="st-check">
        <input type="checkbox" checked={onlyAdult} onChange={(e) => setOnlyAdult(e.target.checked)} />
        Só fotos e vídeos marcados como +18 ou sensíveis
      </label>
      {library.length === 0 ? (
        <p className="st-hint">Nada para escolher na biblioteca.</p>
      ) : (
        <div className="st-picker">
          {library.map((item) => {
            const n = picked.indexOf(item.path);
            return (
              <div key={item.path} className={n >= 0 ? "st-pick on" : "st-pick"}>
                <Thumb item={item} onClick={() => setPicked((p) => (n >= 0 ? p.filter((x) => x !== item.path) : [...p, item.path]))} />
                {n >= 0 && <span className="st-pick-n">{n + 1}</span>}
                <span className="st-pick-name">{item.title}</span>
              </div>
            );
          })}
        </div>
      )}
      <div className="st-composer-actions">
        <span className="st-hint">{picked.length} escolhido(s)</span>
        <button type="button" className="st-btn st-btn-primary" disabled={!picked.length} onClick={() => onPick(picked.map((p) => items.find((i) => i.path === p)!))}>
          Adicionar em {folder.label}
        </button>
      </div>
    </Modal>
  );
}

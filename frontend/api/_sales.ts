import type { IncomingMessage, ServerResponse } from "http";
import { PROMO_USD_MIN, promoActive, promoConfig, savePromo, weekdayInBrazil } from "./_sales_promo.js";
import { randomUUID } from "crypto";
import { isAuthenticated } from "./_auth.js";
import { UPLOAD_BUCKET } from "./_posts.js";
import { Problem } from "./_posts_api.js";
import {
  forgetSalesToken,
  looksLikeSalesToken,
  registerSalesWebhook,
  salesToken,
  saveSalesToken,
  sendMenu,
  type FanRow,
  type ProductRow,
} from "./_sales_bot.js";
import { forgetPix, giftToFan, GIFT_GATEWAY, pixConfig, previewPack, savePixConfig, STARS_GATEWAY } from "./_sales_pay.js";
import { saveStars, STARS_MAX, STARS_MIN, starsConfig, starsPrice, suggestedStars } from "./_sales_stars.js";
import { checkIntlKey, forgetIntl, INTL_GATEWAY, INTL_MAX_CENTS, INTL_MIN_CENTS, intlConfig, intlPrice, saveIntl, suggestedUsd } from "./_sales_intl.js";
import { cancelChamada, chamadaStatus, prepareChamada, startChamada } from "./_sales_chamada.js";
import { ctaConfig, ctaDays, fill, previews, saveCtaConfig, savePreviews, sendCta, suggestCaptions, translateCta } from "./_sales_cta.js";
import { storeConfigured, storeDelete, storeGet, storeSet } from "./_store.js";
import { audit, dbDelete, dbInsert, dbSelect, dbUpdate, storageReadUrl, storageRemove, storageUploadUrl, supabaseConfigured } from "./_supabase.js";
import { telegram, telegramChannel, telegramConfig } from "./_telegram.js";
import { DEFAULT_PERSONA, personaId, personaPath, personaText, runAs } from "./_persona.js";

// /api/studio?r=sales - pagina Vendas do estudio (com login).
//   GET                                  estado: bot, pacotes com o conteudo, fas, pedidos
//   POST { action: "token", token }      confere o token do bot de vendas (getMe) e guarda
//   POST { action: "connect" }           liga o webhook do bot de vendas
//   POST { action: "stars-save", enabled, prices }  estrelas do Telegram (fora do Brasil)
//   POST { action: "intl-save", enabled, apiKey?, ipnSecret?, prices }  pagamento internacional (NOWPayments)
//   POST { action: "intl-forget" }       apaga as chaves da NOWPayments
//   POST { action: "chamada-prepare" }   chamada para quem nao pagou: monta e manda o exemplo ao administrador
//   POST { action: "chamada-send", id }  envia a chamada preparada (aos poucos)
//   POST { action: "chamada-cancel", id } para a chamada
//   POST { action: "forget" }            desliga o bot de vendas
//   POST { action: "product", id, ... }  muda nome, descricao, preco, dias de acesso, ativo
//   POST { action: "media-url", product_id, mime_type, size_bytes }
//                                        link para subir a copia temporaria de uma foto/video
//   POST { action: "media-add", product_id, media_id, path }
//                                        o bot de vendas manda a copia para o administrador
//                                        (sem som), guarda o file_id do Telegram e apaga a
//                                        copia: o pacote fica no Telegram, nao no servidor
//   POST { action: "media-remove", product_id, media_id }
//   POST { action: "test" }              o bot de vendas manda o menu para o administrador
//   POST { action: "gift", product_id }  cortesia: libera o pacote/VIP para o administrador,
//                                        entregue igual a uma compra (sem Pix, fora das vendas)
//   POST { action: "pix", token, env }   guarda o token da PushinPay (producao ou sandbox)
//   POST { action: "pix-forget" }        desliga o Pix (os pacotes voltam a "quase pronto")
//   POST { action: "vip-url", mime_type, size_bytes }  link para a copia de um arquivo da pasta do VIP
//   POST { action: "vip-post", media_id, path }        o bot do canal posta o arquivo no canal VIP
//   POST { action: "vip-unpost", media_id }            apaga do canal o post daquele arquivo
//   POST { action: "preview-url" | "preview-add" | "preview-remove" }  previas das mensagens (CTA)
//   POST { action: "cta-save", enabled, hour, welcome, pixReminder, daily }  textos das mensagens
//   POST { action: "cta-test" }                        manda uma mensagem diaria de teste para voce
//   POST { action: "preview-suggest", image }          a IA (com visao) olha a previa e sugere 3 chamadas
//   POST { action: "preview-text", media_id, text }    guarda a chamada daquela previa (vazio tira)
//   POST { action: "cta-days", start, days }           calendario: a diaria de cada dia (✓ saiu / ✗ falhou)
//   POST { action: "promo-save", enabled, days, label, labelEn, prices, stars, usd }
//                                        promocao (ex.: de domingo) por pacote, em reais, estrelas e dolar
//   POST { action: "cta-translate" }     a IA escreve as mensagens em ingles (volta para conferir, nao salva)
//
// Pastas do Telegram (pagina Telegram): conteudos/vendas/ensaio e album-vip viram
// o conteudo dos pacotes (media-add/media-remove); conteudos/vendas/vip e postado
// no canal VIP. Quais arquivos ja estao no canal fica no Upstash (luna:vip-posts).
//
// Para guardar o conteudo dos pacotes, o administrador precisa ter aberto o
// bot de vendas (Comecar): e na conversa com ele que o bot sobe as midias.

const MEDIA_MAX = 40; // por pacote
const VIP_POSTS_KEY = "luna:vip-posts";

// arquivo da pasta do VIP -> mensagens dele no canal
async function vipPosts(): Promise<Record<string, number[]>> {
  if (!storeConfigured()) return {};
  try {
    const map = JSON.parse((await storeGet(VIP_POSTS_KEY)) ?? "{}");
    return map && typeof map === "object" && !Array.isArray(map) ? map : {};
  } catch {
    return {};
  }
}

async function saveVipPosts(map: Record<string, number[]>): Promise<void> {
  await storeSet(VIP_POSTS_KEY, JSON.stringify(map));
}

async function channelBot(): Promise<{ token: string; channelId: string }> {
  const [channel, admin] = await Promise.all([telegramChannel(), telegramConfig()]);
  if (!channel || !admin.token) throw new Problem(400, "Conecte o canal +18 em Meu perfil → Canal +18 antes de postar no VIP.");
  return { token: admin.token, channelId: channel.id };
}
const MIMES: Record<string, string> = { "image/jpeg": "jpg", "video/mp4": "mp4" };

interface ProductMediaRow {
  product_id: string;
  media_id: string;
  position: number;
  kind: "photo" | "video";
}

function siteUrl(req: IncomingMessage): string {
  const host = String(req.headers["x-forwarded-host"] ?? req.headers.host ?? "");
  const proto = String(req.headers["x-forwarded-proto"] ?? "https").split(",")[0];
  return `${proto}://${host}`;
}

function uuid(v: unknown, what: string): string {
  if (typeof v !== "string" || !/^[0-9a-f-]{36}$/i.test(v)) throw new Problem(400, `${what} inválido.`);
  return v;
}

async function getProduct(id: unknown): Promise<ProductRow> {
  const [p] = await dbSelect<ProductRow>("sales_products", `select=*&id=eq.${uuid(id, "Pacote")}`);
  if (!p) throw new Problem(404, "Pacote não encontrado.");
  return p;
}

// conversa do administrador com o bot de vendas (o chat privado tem o id do usuario)
async function adminChat(): Promise<number> {
  const config = await telegramConfig();
  if (!config.chatId) throw new Problem(400, "Conecte primeiro o seu bot de aprovações em Meu perfil.");
  const [me] = await dbSelect<FanRow>("fans", `select=*&telegram_id=eq.${config.chatId}`);
  if (!me) throw new Problem(400, "Abra o bot de vendas no seu Telegram e toque em Começar: é na conversa com você que ele guarda as fotos dos pacotes.");
  return me.telegram_id;
}

// O bot de vendas manda a copia para o administrador (sem som), guarda o file_id
// do Telegram, apaga a mensagem e a copia: o arquivo fica no Telegram, nao aqui.
// keep: a copia continua no armazenamento (publicacao +18 que ainda vai para o canal)
async function storeOnTelegram(token: string, path: string, caption: string, keep = false): Promise<{ fileId: string; kind: "photo" | "video" }> {
  const chat = await adminChat();
  const video = path.endsWith(".mp4");
  try {
    const url = await storageReadUrl(UPLOAD_BUCKET, path, 600);
    const sent = await telegram<{ message_id: number; photo?: { file_id: string }[]; video?: { file_id: string }; animation?: { file_id: string } }>(token, video ? "sendVideo" : "sendPhoto", {
      chat_id: chat,
      [video ? "video" : "photo"]: url,
      caption,
      disable_notification: true,
    });
    const fileId = video ? sent.video?.file_id : sent.photo?.at(-1)?.file_id;
    if (!fileId) {
      await telegram(token, "deleteMessage", { chat_id: chat, message_id: sent.message_id }).catch(() => undefined);
      // MP4 sem faixa de som: o Telegram transforma em GIF e nao guarda como video
      if (video && sent.animation) throw new Problem(400, "Esse vídeo está sem som: o Telegram trata como GIF. Deixe o robô do PC aberto (ele põe uma trilha muda nos vídeos borrados) ou use um vídeo com som.");
      throw new Problem(502, "O Telegram não devolveu o arquivo.");
    }
    await telegram(token, "deleteMessage", { chat_id: chat, message_id: sent.message_id }).catch(() => undefined);
    return { fileId, kind: video ? "video" : "photo" };
  } finally {
    if (!keep) await storageRemove(UPLOAD_BUCKET, [path]).catch(() => undefined);
  }
}

// --- aprovacao das pastas no Telegram (pedido de 30/10) ----------------------------------------------
// Arquivo novo nas pastas de vendas (Ensaio, Album VIP, VIP canal, Previas) nao entra direto: a copia
// sobe, o bot de aprovacoes manda a foto/video com "Aprovar / Recusar" e so na aprovacao entra no
// pacote, sai no canal ou vira previa. Recusada: a copia sai e o estudio move o arquivo para
// vendas/_removidos. Pendentes e recusadas ficam no Upstash (o estudio mostra "no Telegram").
export type FolderTarget = "pack" | "vip" | "previa";
export interface FolderPending {
  id: string;
  target: FolderTarget;
  product_id?: string;
  media_id: string;
  path: string;
  label: string;
  at: number;
  // previa: chamada escrita antes de aprovar no Telegram (vale quando aprovar)
  text?: string;
}
const FOLDER_KEY = (id: string) => `luna:pasta:${id}`;
const FOLDER_INDEX = "luna:pasta-pendentes";
const FOLDER_REJECTED = "luna:pasta-recusadas";
const FOLDER_TTL_MS = 7 * 86400_000;

async function readList<T>(key: string): Promise<T[]> {
  try {
    const list = JSON.parse((await storeGet(key)) ?? "[]");
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export async function folderPending(): Promise<FolderPending[]> {
  // pendente de mais de 7 dias: a aprovacao expirou
  return (await readList<FolderPending>(FOLDER_INDEX)).filter((p) => Date.now() - p.at < FOLDER_TTL_MS);
}

export async function folderRejected(): Promise<{ media_id: string; target: FolderTarget }[]> {
  return readList(FOLDER_REJECTED);
}

function targetPath(target: FolderTarget, productId: string | undefined, path: string): boolean {
  if (target === "pack") return Boolean(productId) && path.startsWith(`vendas/${productId}/`) && /^vendas\/[0-9a-f-]{36}\/[0-9a-f-]{36}\.(jpg|mp4)$/.test(path);
  if (target === "vip") return /^vendas\/vip\/[0-9a-f-]{36}\.(jpg|mp4)$/.test(path);
  return /^vendas\/previa\/[0-9a-f-]{36}\.(jpg|mp4)$/.test(path);
}

// Manda para aprovar no Telegram (a copia ja subiu). false = ja estava esperando.
export async function sendFolderReview(input: Omit<FolderPending, "id" | "at">): Promise<boolean> {
  if (!storeConfigured()) throw new Problem(501, "Falta conectar o banco Upstash Redis ao projeto na Vercel (aba Storage).");
  if (!targetPath(input.target, input.product_id, input.path)) throw new Problem(400, "Arquivo inválido.");
  const list = await folderPending();
  if (list.some((p) => p.media_id === input.media_id && p.target === input.target && p.product_id === input.product_id)) {
    await storageRemove(UPLOAD_BUCKET, [input.path]).catch(() => undefined);
    return false;
  }
  const config = await telegramConfig();
  if (!config.token || !config.chatId) throw new Problem(400, "Conecte o bot de aprovações em Meu perfil para aprovar as pastas pelo Telegram.");
  const pending: FolderPending = { ...input, id: randomUUID(), at: Date.now() };
  await storeSet(FOLDER_KEY(pending.id), JSON.stringify(pending));
  await storeSet(FOLDER_INDEX, JSON.stringify([...list, pending].slice(-200)));
  const video = input.path.endsWith(".mp4");
  const url = await storageReadUrl(UPLOAD_BUCKET, input.path, 6 * 3600);
  await telegram(config.token, video ? "sendVideo" : "sendPhoto", {
    chat_id: config.chatId,
    [video ? "video" : "photo"]: url,
    caption: `📁 Para aprovar — vai para: ${input.label}`,
    reply_markup: {
      inline_keyboard: [
        [
          { text: "✅ Aprovar", callback_data: `fa:${pending.id}` },
          { text: "❌ Recusar", callback_data: `fr:${pending.id}` },
        ],
        // canal: arquivo que voce ja postou a mao antes (nao posta de novo)
        ...(input.target === "vip" ? [[{ text: "📌 Já está no canal (não postar)", callback_data: `fk:${pending.id}` }]] : []),
      ],
    },
  });
  await audit("pasta_para_aprovar", "media_assets", input.media_id, { destino: input.label });
  return true;
}

async function applyFolderAdd(p: FolderPending): Promise<void> {
  if (p.target === "vip") {
    const { token: botToken, channelId } = await channelBot();
    const posts = await vipPosts();
    try {
      if (!posts[p.media_id]) {
        const video = p.path.endsWith(".mp4");
        const url = await storageReadUrl(UPLOAD_BUCKET, p.path, 600);
        const sent = await telegram<{ message_id: number }>(botToken, video ? "sendVideo" : "sendPhoto", { chat_id: channelId, [video ? "video" : "photo"]: url });
        posts[p.media_id] = [sent.message_id];
        await saveVipPosts(posts);
        await audit("vip_postar", "media_assets", p.media_id, { mensagem: sent.message_id });
      }
    } finally {
      await storageRemove(UPLOAD_BUCKET, [p.path]).catch(() => undefined);
    }
    return;
  }
  const token = await salesToken();
  if (!token) throw new Problem(400, "O bot de vendas não está conectado.");
  if (p.target === "previa") {
    const list = await previews();
    if (list.some((x) => x.media_id === p.media_id)) {
      await storageRemove(UPLOAD_BUCKET, [p.path]).catch(() => undefined);
      return;
    }
    const { fileId, kind } = await storeOnTelegram(token, p.path, "Guardada nas prévias das mensagens.");
    await savePreviews([...list, { media_id: p.media_id, kind, file_id: fileId, ...(p.text ? { text: p.text } : {}) }]);
    await audit("previa_cta", "media_assets", p.media_id);
    return;
  }
  const product = await getProduct(p.product_id);
  const current = await dbSelect<ProductMediaRow>("sales_product_media", `select=product_id,media_id,position,kind&product_id=eq.${product.id}`);
  if (current.length >= MEDIA_MAX) throw new Problem(400, `Cada pacote pode ter até ${MEDIA_MAX} fotos e vídeos.`);
  const { fileId, kind } = await storeOnTelegram(token, p.path, `Guardado no pacote “${product.title}”.`);
  await dbDelete("sales_product_media", `product_id=eq.${product.id}&media_id=eq.${p.media_id}`);
  await dbInsert("sales_product_media", {
    product_id: product.id,
    media_id: p.media_id,
    position: current.filter((m) => m.media_id !== p.media_id).length,
    kind,
    telegram_file_id: fileId,
  });
  await audit("pacote_midia", "sales_products", product.id, { media_id: p.media_id });
}

// Botao do Telegram: aprova (entra) ou recusa (a copia sai). Devolve a resposta para o bot.
export async function decideFolder(id: string, decision: "approve" | "reject" | "already"): Promise<string> {
  const raw = await storeGet(FOLDER_KEY(id)).catch(() => null);
  const pending = raw ? (JSON.parse(raw) as FolderPending) : null;
  if (!pending) return "⏱️ Essa aprovação não existe mais (já decidida ou expirou).";
  const approve = decision !== "reject";
  if (decision === "already") {
    // ja esta no canal (postado a mao): so marca como postado, sem mandar de novo
    const posts = await vipPosts();
    if (!posts[pending.media_id]) posts[pending.media_id] = [];
    await saveVipPosts(posts);
    await storageRemove(UPLOAD_BUCKET, [pending.path]).catch(() => undefined);
  } else if (approve) await applyFolderAdd(pending);
  else {
    await storageRemove(UPLOAD_BUCKET, [pending.path]).catch(() => undefined);
    const rejected = await folderRejected();
    const next = [...rejected.filter((r) => r.media_id !== pending.media_id), { media_id: pending.media_id, target: pending.target }];
    await storeSet(FOLDER_REJECTED, JSON.stringify(next.slice(-200)));
  }
  await storeDelete(FOLDER_KEY(id)).catch(() => undefined);
  await storeSet(FOLDER_INDEX, JSON.stringify((await folderPending()).filter((p) => p.id !== id)));
  await audit(approve ? "pasta_aprovada" : "pasta_recusada", "media_assets", pending.media_id, { destino: pending.label });
  if (decision === "already") return "📌 Marcado como já postado: não postei de novo no canal.";
  return approve
    ? `✅ Aprovada: entrou em ${pending.label}.`
    : `❌ Recusada: não entrou em ${pending.label}. O estúdio tira o arquivo da pasta (vai para vendas/_removidos) quando você abrir Conteúdo do Telegram.`;
}

// Publicacao +18 aprovada com pacotes (flags.adultDest): no horario, cada foto
// ou video da versao aprovada entra no conteudo do pacote (como o media-add da
// pasta do Telegram). Repetir nao duplica: a mesma midia so troca de lugar.
// Devolve o nome dos pacotes; erro (bot de vendas desligado etc.) sobe.
export async function addPostToPacks(postId: string, version: number, slugs: string[]): Promise<string[]> {
  const token = await salesToken();
  if (!token) throw new Error("o bot de vendas não está conectado");
  const [uploads, media] = await Promise.all([
    dbSelect<{ position: number; storage_path: string }>(
      "post_uploads",
      `select=position,storage_path&post_id=eq.${postId}&post_version=eq.${version}&deleted_at=is.null&order=position.asc`
    ),
    dbSelect<{ media_id: string; position: number }>("post_media", `select=media_id,position&post_id=eq.${postId}&order=position.asc`),
  ]);
  if (!uploads.length) throw new Error("a cópia aprovada da mídia não está mais no armazenamento");
  const done: string[] = [];
  for (const slug of slugs) {
    const [p] = await dbSelect<ProductRow>("sales_products", `select=*&slug=eq.${encodeURIComponent(slug)}`);
    if (!p) throw new Error(`pacote “${slug}” não existe`);
    for (const up of uploads) {
      const mediaId = media.find((m) => m.position === up.position)?.media_id;
      if (!mediaId) continue;
      const current = await dbSelect<ProductMediaRow>("sales_product_media", `select=product_id,media_id,position,kind&product_id=eq.${p.id}`);
      const others = current.filter((m) => m.media_id !== mediaId);
      if (others.length >= MEDIA_MAX) throw new Error(`o pacote “${p.title}” já tem ${MEDIA_MAX} fotos e vídeos`);
      const { fileId, kind } = await storeOnTelegram(token, up.storage_path, `Guardado no pacote “${p.title}”.`, true);
      await dbDelete("sales_product_media", `product_id=eq.${p.id}&media_id=eq.${mediaId}`);
      await dbInsert("sales_product_media", { product_id: p.id, media_id: mediaId, position: others.length, kind, telegram_file_id: fileId });
    }
    await audit("pacote_midia", "sales_products", p.id, { post_id: postId });
    done.push(p.title);
  }
  return done;
}

// Pacotes na hora da aprovacao (pedido de 31/10): a publicacao +18 aprovada ja
// entra nos pacotes escolhidos; so o post do canal espera o horario. O registro
// em media_usage (context "pack") marca que entrou, e o agendamento nao repete.
export async function postInPacks(postId: string): Promise<boolean> {
  const [media, used] = await Promise.all([
    dbSelect<{ media_id: string }>("post_media", `select=media_id&post_id=eq.${postId}`),
    dbSelect<{ media_id: string }>("media_usage", `select=media_id&reference=eq.${postId}&context=eq.pack`),
  ]);
  const done = new Set(used.map((u) => u.media_id));
  return media.length > 0 && media.every((m) => done.has(m.media_id));
}

export async function packPostNow(postId: string, version: number, slugs: string[]): Promise<string[]> {
  const names = await addPostToPacks(postId, version, slugs);
  for (const m of await dbSelect<{ media_id: string }>("post_media", `select=media_id&post_id=eq.${postId}`)) {
    await dbInsert("media_usage", { media_id: m.media_id, channel: "telegram", context: "pack", reference: postId });
  }
  return names;
}

async function status() {
  const token = await salesToken();
  let bot: string | null = null;
  let hooked = false;
  if (token) {
    bot = await telegram<{ username?: string }>(token, "getMe").then((m) => m.username ?? null).catch(() => null);
    hooked = await telegram<{ url?: string }>(token, "getWebhookInfo")
      .then((w) => Boolean(w.url?.endsWith(`/api/vendas/telegram${personaPath()}`)))
      .catch(() => false);
  }
  const config = await telegramConfig();
  const [products, media, fans, orders] = await Promise.all([
    dbSelect<ProductRow>("sales_products", "select=*&order=position.asc"),
    dbSelect<ProductMediaRow>("sales_product_media", "select=product_id,media_id,position,kind&order=position.asc"),
    dbSelect<FanRow & { started_at: string }>("fans", "select=id,telegram_id,username,first_name,adult_confirmed_at,opted_out_at,blocked_at,started_at"),
    dbSelect<{ id: string; status: string; amount_cents: number; created_at: string; paid_at: string | null; access_until: string | null; product_id: string; fan_id: string; note: string | null; gateway: string | null }>(
      "sales_orders",
      "select=id,status,amount_cents,created_at,paid_at,access_until,product_id,fan_id,note,gateway&order=created_at.desc&limit=200"
    ),
  ]);
  const vipIds = Object.keys(await vipPosts());
  const previewList = await previews();
  // publicacoes do Chat +18 (31/10): a tela das pastas mostra o que veio de la
  // (fora das pastas do PC) e o que ainda vai sair no horario
  const since = new Date(Date.now() - 30 * 86400_000).toISOString();
  const chatPosts = await dbSelect<{ id: string; status: string; scheduled_at: string | null; flags: { adultDest?: { channel?: boolean; packs?: string[] } } | null }>(
    "posts",
    `select=id,status,scheduled_at,flags&network=eq.telegram&status=in.(agendado,publicando,publicado)&created_at=gt.${since}&order=scheduled_at.desc&limit=30`
  );
  const chatMedia = chatPosts.length
    ? await dbSelect<{ post_id: string; media_id: string; position: number }>("post_media", `select=post_id,media_id,position&post_id=in.(${chatPosts.map((p) => p.id).join(",")})&order=position.asc`)
    : [];
  const ids = [...new Set([...media.map((m) => m.media_id), ...vipIds, ...previewList.map((p) => p.media_id), ...chatMedia.map((m) => m.media_id)])];
  const paths = ids.length ? await dbSelect<{ id: string; local_path: string }>("media_assets", `select=id,local_path&id=in.(${ids.join(",")})`) : [];
  const pathOf = new Map(paths.map((m) => [m.id, m.local_path]));
  const channel = await telegramChannel().catch(() => null);
  const pix = await pixConfig();
  const fanOf = new Map(fans.map((f) => [f.id, f.username ? `@${f.username}` : f.first_name || `fã ${f.telegram_id}`]));
  const titleOf = new Map(products.map((p) => [p.id, p.title]));
  // (cortesia do administrador nao e venda)
  const paidOrders = orders.filter((o) => (o.status === "entregue" || o.status === "expirado" || o.status === "pago") && o.gateway !== GIFT_GATEWAY);
  // em reais (Pix); as estrelas contam a parte
  const sold = paidOrders.filter((o) => o.gateway !== STARS_GATEWAY && o.gateway !== INTL_GATEWAY);
  const soldIntl = paidOrders.filter((o) => o.gateway === INTL_GATEWAY);
  const soldStars = paidOrders.filter((o) => o.gateway === STARS_GATEWAY);
  const month = Date.now() - 30 * 86400_000;
  // quem esta no VIP agora (ate 3 dias depois de vencer a pessoa ainda fica no canal)
  const vipProducts = new Set(products.filter((p) => p.kind === "vip").map((p) => p.id));
  const vipUntil = new Map<string, number>();
  for (const o of orders) {
    if (o.status !== "entregue" || !o.access_until || !vipProducts.has(o.product_id)) continue;
    vipUntil.set(o.fan_id, Math.max(vipUntil.get(o.fan_id) ?? 0, Date.parse(o.access_until)));
  }
  // quantas pessoas o Telegram conta no canal VIP (inclui os administradores)
  const channelMembers =
    channel && config.token
      ? await telegram<number>(config.token, "getChatMemberCount", { chat_id: channel.id }).then((n) => (typeof n === "number" ? n : null)).catch(() => null)
      : null;
  const promo = await promoConfig();
  const vipMembers = [...vipUntil]
    .filter(([, until]) => until > Date.now() - 3 * 86400_000)
    .sort((a, b) => a[1] - b[1])
    .map(([fanId, until]) => ({ fan: fanOf.get(fanId) ?? "?", until: new Date(until).toISOString() }));
  return {
    storeReady: storeConfigured(),
    tokenSet: Boolean(token),
    bot,
    connected: hooked,
    adminStarted: Boolean(config.chatId && fans.some((f) => String(f.telegram_id) === config.chatId)),
    // video da chamada (24/11)
    callVideo: await (async () => {
      const { callVideo, broadcastStatus, DEFAULT_BROADCAST } = await import("./_sales_video.js");
      const v = await callVideo();
      return { video: v ? { link: v.link, kind: v.kind, at: v.at } : null, broadcast: await broadcastStatus().catch(() => null), defaults: DEFAULT_BROADCAST };
    })().catch(() => null),
    channel: channel?.title ?? null,
    products: products.map((p) => ({
      ...p,
      media: media.filter((m) => m.product_id === p.id).map((m) => ({ media_id: m.media_id, kind: m.kind, path: pathOf.get(m.media_id) ?? null })),
    })),
    fans: {
      total: fans.length,
      adults: fans.filter((f) => f.adult_confirmed_at).length,
      optedOut: fans.filter((f) => f.opted_out_at).length,
      blocked: fans.filter((f) => f.blocked_at).length,
      today: fans.filter((f) => Date.now() - Date.parse(f.started_at) < 86400_000).length,
    },
    pix: { configured: Boolean(pix.token), env: pix.env },
    // estudo do publico (23/11): lingua do Telegram e Brasil/fora de quem entra
    study: await (await import("./_sales_lang.js")).audienceStudy().catch(() => null),
    cta: await ctaConfig(),
    previews: previewList.map((p) => ({ media_id: p.media_id, kind: p.kind, path: pathOf.get(p.media_id) ?? null, text: p.text ?? "" })),
    // pastas: esperando a aprovacao no Telegram e recusadas (o estudio move para _removidos)
    folderPending: storeConfigured() ? (await folderPending()).map((p) => ({ media_id: p.media_id, target: p.target, product_id: p.product_id ?? null, ...(p.text ? { text: p.text } : {}) })) : [],
    folderRejected: storeConfigured() ? await folderRejected() : [],
    // arquivo da pasta do VIP ja postado no canal (o caminho fica no banco mesmo se o arquivo sair da pasta)
    vipPosts: vipIds.map((id) => ({ media_id: id, path: pathOf.get(id) ?? null })),
    chat18: chatPosts.map((p) => ({
      post_id: p.id,
      status: p.status,
      scheduled_at: p.scheduled_at,
      // sem escolha (pedido antigo): so o canal
      channel: p.flags?.adultDest ? p.flags.adultDest.channel !== false : true,
      packs: p.flags?.adultDest?.packs ?? [],
      media: chatMedia.filter((m) => m.post_id === p.id).map((m) => ({ media_id: m.media_id, path: pathOf.get(m.media_id) ?? null })),
    })),
    sales: {
      count30: sold.filter((o) => Date.parse(o.paid_at ?? o.created_at) > month).length,
      total30: sold.filter((o) => Date.parse(o.paid_at ?? o.created_at) > month).reduce((t, o) => t + o.amount_cents, 0),
      pending: orders.filter((o) => o.status === "pendente" && o.gateway !== STARS_GATEWAY && o.gateway !== INTL_GATEWAY).length,
      intlCount30: soldIntl.filter((o) => Date.parse(o.paid_at ?? o.created_at) > month).length,
      intl30: soldIntl.filter((o) => Date.parse(o.paid_at ?? o.created_at) > month).reduce((t, o) => t + o.amount_cents, 0),
      starsCount30: soldStars.filter((o) => Date.parse(o.paid_at ?? o.created_at) > month).length,
      stars30: soldStars.filter((o) => Date.parse(o.paid_at ?? o.created_at) > month).reduce((t, o) => t + o.amount_cents, 0),
    },
    // meta do ano (23/11): R$ 10 mil de 01/11 a 31/12, somando Luna e Cleuza
    goal: await goalProgress().catch(() => null),
    orders: orders.slice(0, 50).map((o) => ({ ...o, product: titleOf.get(o.product_id) ?? "?", fan: fanOf.get(o.fan_id) ?? "?" })),
    vipMembers,
    channelMembers,
    promo: { ...promo, active: promoActive(promo), today: weekdayInBrazil() },
    stars: { ...(await starsConfig()), suggested: Object.fromEntries(products.map((p) => [p.id, suggestedStars(p)])) },
    // as chaves nunca voltam ao navegador: so se estao coladas
    intl: await intlConfig().then((c) => ({
      enabled: c.enabled,
      configured: Boolean(c.apiKey && c.ipnSecret),
      apiKeySet: Boolean(c.apiKey),
      ipnSecretSet: Boolean(c.ipnSecret),
      prices: c.prices,
      suggested: Object.fromEntries(products.map((p) => [p.id, suggestedUsd(p)])),
    })),
    chamada: await chamadaStatus().catch(() => null),
  };
}

// --- meta (pedido de 23/11: "bater nossa primeira meta, 10k vendidos ate o final do ano") ---
// Vendas pagas de 01/11 a 31/12 de todas as personas, em reais: Pix pelo valor;
// estrelas pelo que cai limpo (~US$ 0,013 por estrela a ~R$ 5,00 = R$ 0,065);
// dolar (cripto) a ~R$ 5,00. Cortesia nao conta.
const GOAL = { cents: 1_000_000, from: "2026-11-01T03:00:00.000Z", until: "2027-01-01T03:00:00.000Z" };
const STAR_BRL_CENTS = 6.5;
const USD_BRL = 5;

async function goalProgress(): Promise<{ target: number; cents: number; sales: number; daysLeft: number }> {
  const { PERSONAS } = await import("./_persona.js");
  let cents = 0;
  let sales = 0;
  for (const id of Object.keys(PERSONAS)) {
    const rows = await runAs(id, () =>
      dbSelect<{ amount_cents: number; gateway: string | null }>(
        "sales_orders",
        `select=amount_cents,gateway&status=in.(pago,entregue,expirado)&paid_at=gte.${GOAL.from}&paid_at=lt.${GOAL.until}`
      )
    );
    for (const o of rows) {
      if (o.gateway === GIFT_GATEWAY) continue;
      sales++;
      cents += o.gateway === STARS_GATEWAY ? Math.round(o.amount_cents * STAR_BRL_CENTS) : o.gateway === INTL_GATEWAY ? o.amount_cents * USD_BRL : o.amount_cents;
    }
  }
  const daysLeft = Math.max(0, Math.ceil((Date.parse(GOAL.until) - Date.now()) / 86400_000));
  return { target: GOAL.cents, cents, sales, daysLeft };
}

export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const reply = (code: number, body: object) => {
    res.statusCode = code;
    res.end(JSON.stringify(body));
  };
  if (!(await isAuthenticated(req))) return reply(401, { detail: "Sessão expirada. Entre de novo." });
  if (!supabaseConfigured()) return reply(501, { detail: "Banco (Supabase) ainda não configurado na Vercel." });

  try {
    if (req.method === "GET") return reply(200, await status());
    if (req.method !== "POST") return reply(405, { detail: "Use GET ou POST." });
    if (!storeConfigured()) throw new Problem(501, "Falta conectar o banco Upstash Redis ao projeto na Vercel (aba Storage).");
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw || "{}") as Record<string, unknown>;
    // calendario: a mensagem diaria de cada dia (so leitura)
    if (body.action === "cta-days") {
      const start = typeof body.start === "string" && /^\d{4}-\d{2}-\d{2}$/.test(body.start) ? body.start : new Date().toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });
      const days = Math.min(42, Math.max(1, Number(body.days ?? 14) || 14));
      return reply(200, { days: await ctaDays(start, days) });
    }
    // Fase 3 (11/11): persona nova copia os planos da Luna (nome, descricao, preco,
    // prazo e cobranca; nome "Luna" trocado). O conteudo dos pacotes nao vem junto.
    if (body.action === "copy-products") {
      if (personaId() === DEFAULT_PERSONA) throw new Problem(400, "Os planos da Luna já são os originais.");
      if ((await dbSelect<{ id: string }>("sales_products", "select=id&limit=1")).length) throw new Problem(400, "Esta persona já tem planos: edite os que estão aqui.");
      const luna = await runAs(DEFAULT_PERSONA, () =>
        dbSelect<{ slug: string; kind: string; title: string; description: string; price_cents: number; period_days: number; billing: string; active: boolean; position: number }>(
          "sales_products",
          "select=slug,kind,title,description,price_cents,period_days,billing,active,position&order=position.asc"
        )
      );
      if (!luna.length) throw new Problem(400, "A Luna ainda não tem planos para copiar.");
      for (const p of luna) {
        await dbInsert("sales_products", { ...p, title: personaText(p.title).slice(0, 80), description: personaText(p.description ?? "").slice(0, 600) });
      }
      await audit("copiar_planos_da_luna", "sales_products", null, { planos: luna.length });
      return reply(200, await status());
    }

    const token = await salesToken();
    const needToken = () => {
      if (!token) throw new Problem(400, "Cole o token do bot de vendas primeiro.");
      return token;
    };

    if (body.action === "token") {
      const value = typeof body.token === "string" ? body.token.trim() : "";
      if (!looksLikeSalesToken(value)) throw new Problem(400, "Isso não parece um token de bot (formato 123456789:ABC...).");
      const config = await telegramConfig();
      if (config.token === value) throw new Problem(400, "Esse é o token do seu bot de aprovações. O bot de vendas precisa ser outro bot (crie no @BotFather).");
      await telegram(value, "getMe").catch(() => {
        throw new Problem(400, "O Telegram não aceitou esse token. Copie de novo do @BotFather.");
      });
      await saveSalesToken(value);
      await audit("bot_vendas_token", "sales", null);
    } else if (body.action === "connect") {
      await registerSalesWebhook(needToken(), siteUrl(req));
      await audit("bot_vendas_conectar", "sales", null);
    } else if (body.action === "forget") {
      if (token) await telegram(token, "deleteWebhook").catch(() => undefined);
      await forgetSalesToken();
      await audit("bot_vendas_desligar", "sales", null);
    } else if (body.action === "product") {
      const p = await getProduct(body.id);
      const patch: Record<string, unknown> = { updated_at: new Date().toISOString() };
      if (typeof body.title === "string") {
        const t = body.title.trim();
        if (!t || t.length > 80) throw new Problem(400, "O nome do pacote precisa ter de 1 a 80 letras.");
        patch.title = t;
      }
      if (typeof body.description === "string") patch.description = body.description.trim().slice(0, 600);
      if (body.price_cents !== undefined) {
        const cents = Number(body.price_cents);
        if (!Number.isInteger(cents) || cents < 100 || cents > 100000) throw new Problem(400, "Preço entre R$ 1,00 e R$ 1.000,00.");
        patch.price_cents = cents;
      }
      if (body.period_days !== undefined) {
        const days = Number(body.period_days);
        if (!Number.isInteger(days) || days < 1 || days > 366) throw new Problem(400, "Prazo de acesso entre 1 e 366 dias.");
        patch.period_days = days;
      }
      if (typeof body.active === "boolean") patch.active = body.active;
      await dbUpdate("sales_products", `id=eq.${p.id}`, patch);
      await audit("editar_pacote", "sales_products", p.id, patch);
    } else if (body.action === "media-url") {
      const p = await getProduct(body.product_id);
      if (p.kind === "vip") throw new Problem(400, "O VIP é o acesso ao canal: o conteúdo dele é o que sai no canal.");
      const ext = MIMES[String(body.mime_type)];
      if (!ext) throw new Problem(400, "Mande foto JPEG ou vídeo MP4.");
      const size = Number(body.size_bytes);
      // limites do Telegram para enviar por link: foto 5 MB, video 20 MB
      const max = ext === "jpg" ? 5 * 1024 * 1024 : 20 * 1024 * 1024;
      if (!Number.isFinite(size) || size <= 0 || size > max) throw new Problem(400, ext === "jpg" ? "Cada foto pode ter no máximo 5 MB." : "Cada vídeo pode ter no máximo 20 MB.");
      const path = `vendas/${p.id}/${randomUUID()}.${ext}`;
      return reply(200, { path, url: await storageUploadUrl(UPLOAD_BUCKET, path) });
    } else if (body.action === "media-add") {
      const p = await getProduct(body.product_id);
      const mediaId = uuid(body.media_id, "Mídia");
      const path = String(body.path ?? "");
      if (!new RegExp(`^vendas/${p.id}/[0-9a-f-]{36}\\.(jpg|mp4)$`).test(path)) throw new Problem(400, "Arquivo inválido.");
      const current = await dbSelect<ProductMediaRow>("sales_product_media", `select=product_id,media_id,position,kind&product_id=eq.${p.id}`);
      if (current.length >= MEDIA_MAX) throw new Problem(400, `Cada pacote pode ter até ${MEDIA_MAX} fotos e vídeos.`);
      const [media] = await dbSelect<{ id: string }>("media_assets", `select=id&id=eq.${mediaId}`);
      if (!media) throw new Problem(404, "Mídia não encontrada na biblioteca.");
      const { fileId, kind } = await storeOnTelegram(needToken(), path, `Guardado no pacote “${p.title}”.`);
      await dbDelete("sales_product_media", `product_id=eq.${p.id}&media_id=eq.${mediaId}`);
      await dbInsert("sales_product_media", {
        product_id: p.id,
        media_id: mediaId,
        position: current.filter((m) => m.media_id !== mediaId).length,
        kind,
        telegram_file_id: fileId,
      });
      await audit("pacote_midia", "sales_products", p.id, { media_id: mediaId });
    } else if (body.action === "review-add") {
      // arquivo novo numa pasta: a copia ja subiu (media-url / vip-url / preview-url); vai para aprovar no Telegram
      const target: FolderTarget = body.target === "vip" || body.target === "previa" ? body.target : "pack";
      const mediaId = uuid(body.media_id, "Mídia");
      const product = target === "pack" ? await getProduct(body.product_id) : null;
      const label = product ? `Pacote ${product.title}` : target === "vip" ? "Canal VIP" : "Prévias da chamada (CTA)";
      const sent = await sendFolderReview({ target, product_id: product?.id, media_id: mediaId, path: String(body.path ?? ""), label });
      return reply(200, { ...(await status()), sent });
    } else if (body.action === "review-clear") {
      // recusadas que o estudio ja tirou da pasta
      const ids = Array.isArray(body.media_ids) ? body.media_ids.map(String) : [];
      await storeSet(FOLDER_REJECTED, JSON.stringify((await folderRejected()).filter((r) => !ids.includes(r.media_id))));
    } else if (body.action === "pack-preview") {
      // o pacote chega na conversa do administrador com o bot de vendas, igual ao fa
      const p = await getProduct(body.product_id);
      const token = await salesToken();
      const chatId = (await telegramConfig()).chatId;
      if (!token || !chatId) throw new Problem(400, "Conecte o bot de vendas e o bot de aprovações primeiro.");
      try {
        await previewPack(p.id, token, Number(chatId));
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        throw new Problem(400, /chat not found|blocked|bot can't initiate/i.test(message) ? "Abra o bot de vendas no seu Telegram e mande /start uma vez (o bot só fala com quem abriu a conversa)." : message);
      }
      return reply(200, { ...(await status()), previewed: p.title });
    } else if (body.action === "media-remove") {
      const p = await getProduct(body.product_id);
      await dbDelete("sales_product_media", `product_id=eq.${p.id}&media_id=eq.${uuid(body.media_id, "Mídia")}`);
      await audit("pacote_tirar_midia", "sales_products", p.id, { media_id: body.media_id });
    } else if (body.action === "pix") {
      const value = typeof body.token === "string" ? body.token.trim() : "";
      if (!/^[A-Za-z0-9|._~+/=-]{20,400}$/.test(value)) throw new Problem(400, "Isso não parece um token da PushinPay. Copie de novo no painel dela (API).");
      await savePixConfig(value, body.env === "sandbox" ? "sandbox" : "producao");
      await audit("pix_configurar", "sales", null, { ambiente: body.env === "sandbox" ? "sandbox" : "producao" });
    } else if (body.action === "pix-forget") {
      await forgetPix();
      await audit("pix_desligar", "sales", null);
    } else if (body.action === "vip-url") {
      await channelBot();
      const ext = MIMES[String(body.mime_type)];
      if (!ext) throw new Problem(400, "Mande foto JPEG ou vídeo MP4.");
      const size = Number(body.size_bytes);
      const max = ext === "jpg" ? 5 * 1024 * 1024 : 20 * 1024 * 1024;
      if (!Number.isFinite(size) || size <= 0 || size > max) throw new Problem(400, ext === "jpg" ? "Cada foto pode ter no máximo 5 MB." : "Cada vídeo pode ter no máximo 20 MB.");
      const path = `vendas/vip/${randomUUID()}.${ext}`;
      return reply(200, { path, url: await storageUploadUrl(UPLOAD_BUCKET, path) });
    } else if (body.action === "vip-post") {
      const mediaId = uuid(body.media_id, "Mídia");
      const path = String(body.path ?? "");
      if (!/^vendas\/vip\/[0-9a-f-]{36}\.(jpg|mp4)$/.test(path)) throw new Problem(400, "Arquivo inválido.");
      const { token: botToken, channelId } = await channelBot();
      const posts = await vipPosts();
      try {
        if (!posts[mediaId]) {
          const video = path.endsWith(".mp4");
          const url = await storageReadUrl(UPLOAD_BUCKET, path, 600);
          const sent = await telegram<{ message_id: number }>(botToken, video ? "sendVideo" : "sendPhoto", { chat_id: channelId, [video ? "video" : "photo"]: url });
          posts[mediaId] = [sent.message_id];
          await saveVipPosts(posts);
          await audit("vip_postar", "media_assets", mediaId, { mensagem: sent.message_id });
        }
      } finally {
        await storageRemove(UPLOAD_BUCKET, [path]).catch(() => undefined);
      }
    } else if (body.action === "vip-unpost") {
      const mediaId = uuid(body.media_id, "Mídia");
      const posts = await vipPosts();
      const ids = posts[mediaId] ?? [];
      let warning: string | null = null;
      if (ids.length) {
        const { token: botToken, channelId } = await channelBot();
        for (const id of ids) {
          await telegram(botToken, "deleteMessage", { chat_id: channelId, message_id: id }).catch(() => {
            warning = "O Telegram não deixou apagar um post antigo do canal: apague à mão no canal (ou dê ao bot a permissão “Apagar mensagens”).";
          });
        }
      }
      delete posts[mediaId];
      await saveVipPosts(posts);
      await audit("vip_apagar", "media_assets", mediaId, { mensagens: ids });
      return reply(200, { ...(await status()), warning });
    } else if (body.action === "preview-url") {
      const ext = MIMES[String(body.mime_type)];
      if (!ext) throw new Problem(400, "Mande foto JPEG ou vídeo MP4.");
      const size = Number(body.size_bytes);
      const max = ext === "jpg" ? 5 * 1024 * 1024 : 20 * 1024 * 1024;
      if (!Number.isFinite(size) || size <= 0 || size > max) throw new Problem(400, ext === "jpg" ? "Cada foto pode ter no máximo 5 MB." : "Cada vídeo pode ter no máximo 20 MB.");
      const path = `vendas/previa/${randomUUID()}.${ext}`;
      return reply(200, { path, url: await storageUploadUrl(UPLOAD_BUCKET, path) });
    } else if (body.action === "preview-add") {
      const mediaId = uuid(body.media_id, "Mídia");
      const path = String(body.path ?? "");
      if (!/^vendas\/previa\/[0-9a-f-]{36}\.(jpg|mp4)$/.test(path)) throw new Problem(400, "Arquivo inválido.");
      const list = await previews();
      if (!list.some((p) => p.media_id === mediaId)) {
        const { fileId, kind } = await storeOnTelegram(needToken(), path, "Guardada nas prévias das mensagens.");
        await savePreviews([...list, { media_id: mediaId, kind, file_id: fileId }]);
      } else {
        await storageRemove(UPLOAD_BUCKET, [path]).catch(() => undefined);
      }
      await audit("previa_cta", "media_assets", mediaId);
    } else if (body.action === "preview-remove") {
      const mediaId = uuid(body.media_id, "Mídia");
      await savePreviews((await previews()).filter((p) => p.media_id !== mediaId));
      await audit("previa_cta_tirar", "media_assets", mediaId);
    } else if (body.action === "preview-suggest") {
      const image = String(body.image ?? "");
      if (!/^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(image) || image.length > 1_500_000) throw new Problem(400, "Mande a prévia como JPEG de até 1 MB.");
      try {
        return reply(200, { suggestions: await suggestCaptions(image) });
      } catch (e) {
        throw new Problem(502, e instanceof Error ? e.message : String(e));
      }
    } else if (body.action === "preview-text") {
      const mediaId = uuid(body.media_id, "Mídia");
      const text = typeof body.text === "string" ? body.text.trim().slice(0, 900) : "";
      const list = await previews();
      if (list.some((p) => p.media_id === mediaId)) {
        await savePreviews(list.map((p) => (p.media_id === mediaId ? { ...p, text: text || undefined } : p)));
      } else {
        // previa ainda esperando a aprovacao no Telegram (31/10): a chamada fica
        // guardada no pedido e entra junto quando aprovar
        const waiting = storeConfigured() ? (await folderPending()).filter((p) => p.media_id === mediaId && p.target === "previa") : [];
        if (!waiting.length) throw new Problem(404, "Essa prévia não está nas mensagens.");
        for (const w of waiting) await storeSet(FOLDER_KEY(w.id), JSON.stringify({ ...w, text: text || undefined }));
        await storeSet(FOLDER_INDEX, JSON.stringify((await folderPending()).map((p) => (waiting.some((w) => w.id === p.id) ? { ...p, text: text || undefined } : p))));
      }
      await audit("previa_chamada", "media_assets", mediaId);
    } else if (body.action === "promo-save") {
      const products = await dbSelect<ProductRow>("sales_products", "select=id,price_cents,title");
      const days = Array.isArray(body.days) ? [...new Set(body.days.map(Number))].filter((d) => Number.isInteger(d) && d >= 0 && d <= 6) : [];
      if (body.enabled && !days.length) throw new Problem(400, "Escolha pelo menos um dia da promoção.");
      const prices: Record<string, number> = {};
      const raw = body.prices && typeof body.prices === "object" ? (body.prices as Record<string, unknown>) : {};
      for (const p of products) {
        if (raw[p.id] === undefined || raw[p.id] === null || raw[p.id] === "") continue;
        const cents = Number(raw[p.id]);
        if (!Number.isInteger(cents) || cents < 100) throw new Problem(400, `Promoção de “${p.title}”: no mínimo R$ 1,00.`);
        if (cents >= p.price_cents) throw new Problem(400, `Promoção de “${p.title}” precisa ser menor que o preço normal.`);
        prices[p.id] = cents;
      }
      // 23/11: tambem em estrelas e em dolar (fa de fora), menor que o preco normal de cada um
      const sc = await starsConfig();
      const ic = await intlConfig();
      const allRows = await dbSelect<ProductRow>("sales_products", "select=*");
      const promoIn = (key: "stars" | "usd") => (body[key] && typeof body[key] === "object" ? (body[key] as Record<string, unknown>) : {});
      const stars: Record<string, number> = {};
      const usd: Record<string, number> = {};
      for (const p of allRows) {
        const s = promoIn("stars")[p.id];
        if (s !== undefined && s !== null && s !== "") {
          const n = Number(s);
          const normal = starsPrice(p, { ...sc, enabled: true });
          if (!Number.isInteger(n) || n < STARS_MIN) throw new Problem(400, `Promoção em estrelas de “${p.title}”: no mínimo ${STARS_MIN} ⭐.`);
          if (normal && n >= normal) throw new Problem(400, `Promoção em estrelas de “${p.title}” precisa ser menor que o normal (${normal} ⭐).`);
          stars[p.id] = n;
        }
        const u = promoIn("usd")[p.id];
        if (u !== undefined && u !== null && u !== "") {
          const n = Number(u);
          const normal = intlPrice(p, { ...ic, enabled: true, apiKey: ic.apiKey ?? "x", ipnSecret: ic.ipnSecret ?? "x" });
          if (!Number.isInteger(n) || n < PROMO_USD_MIN) throw new Problem(400, `Promoção em dólar de “${p.title}”: no mínimo US$ ${PROMO_USD_MIN / 100} (mínimo da NOWPayments).`);
          if (normal && n >= normal) throw new Problem(400, `Promoção em dólar de “${p.title}” precisa ser menor que o normal (US$ ${(normal / 100).toFixed(2)}).`);
          usd[p.id] = n;
        }
      }
      if (body.enabled && !Object.keys(prices).length && !Object.keys(stars).length && !Object.keys(usd).length) throw new Problem(400, "Coloque o preço da promoção em pelo menos um pacote.");
      const label = typeof body.label === "string" && body.label.trim() ? body.label.trim().slice(0, 60) : "Promoção de domingo";
      const labelEn = typeof body.labelEn === "string" && body.labelEn.trim() ? body.labelEn.trim().slice(0, 60) : "Weekend deal";
      await savePromo({ enabled: Boolean(body.enabled), days, label, labelEn, prices, stars, usd });
      await audit("promocao", "sales", null, { ligada: Boolean(body.enabled), dias: days, pacotes: Object.keys(prices).length, estrelas: Object.keys(stars).length, dolar: Object.keys(usd).length });
    } else if (body.action === "stars-save") {
      const products = await dbSelect<ProductRow>("sales_products", "select=id,title");
      const prices: Record<string, number> = {};
      const raw = body.prices && typeof body.prices === "object" ? (body.prices as Record<string, unknown>) : {};
      for (const p of products) {
        if (raw[p.id] === undefined || raw[p.id] === null || raw[p.id] === "") continue;
        const stars = Number(raw[p.id]);
        if (!Number.isInteger(stars) || stars < STARS_MIN || stars > STARS_MAX) throw new Problem(400, `Estrelas de “${p.title}”: de ${STARS_MIN} a ${STARS_MAX}.`);
        prices[p.id] = stars;
      }
      await saveStars({ enabled: Boolean(body.enabled), prices });
      // o webhook passa a receber a pergunta do Telegram antes de cobrar (pre_checkout_query)
      if (body.enabled) await registerSalesWebhook(needToken(), siteUrl(req));
      await audit("estrelas", "sales", null, { ligadas: Boolean(body.enabled), pacotes: Object.keys(prices).length });
    } else if (body.action === "intl-save") {
      const current = await intlConfig();
      const apiKey = typeof body.apiKey === "string" && body.apiKey.trim() ? body.apiKey.trim() : current.apiKey;
      const ipnSecret = typeof body.ipnSecret === "string" && body.ipnSecret.trim() ? body.ipnSecret.trim() : current.ipnSecret;
      if (apiKey && !/^[A-Za-z0-9-]{10,80}$/.test(apiKey)) throw new Problem(400, "Essa não parece a chave da API da NOWPayments (Settings → Payments → API keys).");
      if (ipnSecret && !/^[A-Za-z0-9+/=_-]{10,120}$/.test(ipnSecret)) throw new Problem(400, "Essa não parece a chave do IPN da NOWPayments (Settings → Payments → Instant payment notifications).");
      if (apiKey && apiKey !== current.apiKey) {
        try {
          await checkIntlKey(apiKey);
        } catch (e) {
          throw new Problem(400, `A NOWPayments recusou a chave da API: ${e instanceof Error ? e.message : String(e)}`);
        }
      }
      if (body.enabled && (!apiKey || !ipnSecret)) throw new Problem(400, "Cole as duas chaves (API e IPN) antes de ligar.");
      const products = await dbSelect<ProductRow>("sales_products", "select=id,title");
      const prices: Record<string, number> = {};
      const raw = body.prices && typeof body.prices === "object" ? (body.prices as Record<string, unknown>) : {};
      for (const p of products) {
        if (raw[p.id] === undefined || raw[p.id] === null || raw[p.id] === "") continue;
        const cents = Number(raw[p.id]);
        if (!Number.isInteger(cents) || cents < INTL_MIN_CENTS || cents > INTL_MAX_CENTS) throw new Problem(400, `Preço em dólar de “${p.title}”: de US$ ${INTL_MIN_CENTS / 100} a US$ ${INTL_MAX_CENTS / 100}.`);
        prices[p.id] = cents;
      }
      await saveIntl({ enabled: Boolean(body.enabled), apiKey, ipnSecret, prices });
      await audit("internacional", "sales", null, { ligado: Boolean(body.enabled), pacotes: Object.keys(prices).length });
    } else if (body.action === "intl-forget") {
      await forgetIntl();
      await audit("internacional_apagado", "sales", null);
    } else if (body.action === "chamada-prepare") {
      try {
        await prepareChamada(needToken());
      } catch (e) {
        if (e instanceof Problem) throw e;
        throw new Problem(400, e instanceof Error ? e.message : String(e));
      }
    } else if (body.action === "chamada-send" || body.action === "chamada-cancel") {
      const id = typeof body.id === "string" ? body.id : "";
      try {
        if (body.action === "chamada-send") await startChamada(id);
        else await cancelChamada(id);
      } catch (e) {
        throw new Problem(400, e instanceof Error ? e.message : String(e));
      }
    } else if (body.action === "cta-save") {
      try {
        await saveCtaConfig(body);
      } catch (e) {
        throw new Problem(400, e instanceof Error ? e.message : String(e));
      }
      await audit("cta_textos", "sales", null);
    } else if (body.action === "cta-translate") {
      // a IA escreve as mensagens em ingles a partir das em portugues; volta para o
      // estudio conferir (so salva quando o administrador tocar em Salvar)
      const config = await ctaConfig();
      const products = await dbSelect<ProductRow>("sales_products", "select=id,title&active=eq.true&order=position.asc");
      try {
        return reply(200, { translated: await translateCta(config, products) });
      } catch (e) {
        throw new Problem(502, e instanceof Error ? e.message : String(e));
      }
    } else if (body.action === "cta-test") {
      // lang "en": como o fa de fora recebe (frase em ingles, botoes em estrelas/dolar)
      const chat = await adminChat();
      const config = await ctaConfig();
      const [me] = await dbSelect<FanRow>("fans", `select=*&telegram_id=eq.${chat}`);
      const en = body.lang === "en";
      const day = Math.floor(Date.now() / 86400_000);
      const text = en ? config.dailyEn[day % config.dailyEn.length] : config.daily[day % config.daily.length];
      await sendCta(needToken(), chat, fill(text, me, {}, en ? "en" : "pt"), Math.floor(Math.random() * 1000), undefined, en ? "en" : "pt");
    } else if (body.action === "test") {
      await sendMenu(needToken(), await adminChat());
    } else if (body.action === "video-import") {
      // video da chamada (24/11): pelo link da mensagem do Telegram
      const { importCallVideo } = await import("./_sales_video.js");
      try {
        await importCallVideo(needToken(), String(body.link ?? ""), await adminChat());
      } catch (e) {
        throw new Problem(400, e instanceof Error ? e.message : String(e));
      }
      await audit("video_chamada", "sales", null, { link: String(body.link ?? "").slice(0, 200) });
    } else if (body.action === "video-clear") {
      const { clearCallVideo } = await import("./_sales_video.js");
      await clearCallVideo();
      await audit("video_chamada_tirar", "sales", null, {});
    } else if (body.action === "video-test") {
      // exemplo no Telegram do administrador: como o fa recebe (texto do envio para todos)
      const { callVideo, sendWithCallVideo } = await import("./_sales_video.js");
      const { buyButtonsFor, fill, promoLine } = await import("./_sales_cta.js");
      const video = await callVideo();
      if (!video) throw new Problem(400, "Escolha o vídeo antes.");
      const chat = await adminChat();
      const lang = body.lang === "en" ? "en" : "pt";
      const text = String((lang === "en" ? body.textEn : body.textPt) ?? "").trim();
      if (!text) throw new Problem(400, "Escreva o texto antes.");
      await sendWithCallVideo(needToken(), chat, video, await promoLine(fill(text, { first_name: "Admin" }, {}, lang), lang), await buyButtonsFor(lang));
    } else if (body.action === "video-send-all") {
      const { startBroadcast } = await import("./_sales_video.js");
      try {
        await startBroadcast(String(body.textPt ?? ""), String(body.textEn ?? ""));
      } catch (e) {
        throw new Problem(400, e instanceof Error ? e.message : String(e));
      }
      await audit("video_chamada_todos", "sales", null, {});
    } else if (body.action === "video-stop") {
      const { stopBroadcast } = await import("./_sales_video.js");
      await stopBroadcast();
    } else if (body.action === "stars-test") {
      // teste do administrador: fatura de 1 estrela do 1o pacote no Telegram dele (compra de verdade)
      const chat = await adminChat();
      const [me] = await dbSelect<FanRow>("fans", `select=*&telegram_id=eq.${chat}`);
      if (!me) throw new Problem(400, "Abra o bot de vendas no seu Telegram e toque em Começar antes de testar.");
      const { sendStarsTest } = await import("./_sales_stars.js");
      try {
        await sendStarsTest(needToken(), me, chat);
      } catch (e) {
        throw new Problem(400, e instanceof Error ? e.message : String(e));
      }
    } else if (body.action === "fan-help") {
      // fa que tentou pagar e nao conseguiu: manda o guia de pagamento com os botoes
      const fanId = String(body.fan_id ?? "");
      if (!/^[0-9a-f-]{36}$/i.test(fanId)) throw new Problem(400, "Fã inválido.");
      const [fan] = await dbSelect<FanRow>("fans", `select=*&id=eq.${fanId}`);
      if (!fan) throw new Problem(404, "Fã não encontrado.");
      if (fan.blocked_at) throw new Problem(400, "Esse fã bloqueou o bot.");
      const { payAttempts, sendPayHelp } = await import("./_sales_help.js");
      const { tried, paid } = await payAttempts(fan);
      if (paid) throw new Problem(400, "Esse fã já pagou nas últimas 24 h.");
      try {
        await sendPayHelp(needToken(), fan.telegram_id, tried);
      } catch (e) {
        throw new Problem(400, `Não consegui mandar: ${e instanceof Error ? e.message : String(e)}`);
      }
      await audit("ajuda_pagamento", "fans", fan.id, {}).catch(() => undefined);
    } else if (body.action === "gift") {
      // cortesia para o proprio administrador: ve o VIP/pacote como o fa que pagou
      const p = await getProduct(body.product_id);
      if (p.kind === "vip" && !(await telegramChannel().catch(() => null))) throw new Problem(400, "Conecte o canal VIP primeiro (Meu perfil → Canal +18).");
      const chat = await adminChat();
      const [me] = await dbSelect<FanRow>("fans", `select=*&telegram_id=eq.${chat}`);
      try {
        await giftToFan(p, me, needToken());
      } catch (e) {
        throw new Problem(400, e instanceof Error ? e.message : String(e));
      }
      return reply(200, { ...(await status()), gifted: p.title });
    } else {
      throw new Problem(400, "Ação desconhecida.");
    }
    return reply(200, await status());
  } catch (e) {
    if (e instanceof Problem) return reply(e.status, { detail: e.message });
    return reply(500, { detail: e instanceof Error ? e.message : String(e) });
  }
}

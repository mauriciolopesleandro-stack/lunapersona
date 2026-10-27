import type { IncomingMessage, ServerResponse } from "http";
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
import { storeConfigured } from "./_store.js";
import { audit, dbDelete, dbInsert, dbSelect, dbUpdate, storageReadUrl, storageRemove, storageUploadUrl, supabaseConfigured } from "./_supabase.js";
import { telegram, telegramChannel, telegramConfig } from "./_telegram.js";

// /api/studio?r=sales - pagina Vendas do estudio (com login).
//   GET                                  estado: bot, pacotes com o conteudo, fas, pedidos
//   POST { action: "token", token }      confere o token do bot de vendas (getMe) e guarda
//   POST { action: "connect" }           liga o webhook do bot de vendas
//   POST { action: "forget" }            desliga o bot de vendas
//   POST { action: "product", id, ... }  muda nome, descricao, preco, prazo do VIP, ativo
//   POST { action: "media-url", product_id, mime_type, size_bytes }
//                                        link para subir a copia temporaria de uma foto/video
//   POST { action: "media-add", product_id, media_id, path }
//                                        o bot de vendas manda a copia para o administrador
//                                        (sem som), guarda o file_id do Telegram e apaga a
//                                        copia: o pacote fica no Telegram, nao no servidor
//   POST { action: "media-remove", product_id, media_id }
//   POST { action: "test" }              o bot de vendas manda o menu para o administrador
//
// Para guardar o conteudo dos pacotes, o administrador precisa ter aberto o
// bot de vendas (Comecar): e na conversa com ele que o bot sobe as midias.

const MEDIA_MAX = 40; // por pacote
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

async function status() {
  const token = await salesToken();
  let bot: string | null = null;
  let hooked = false;
  if (token) {
    bot = await telegram<{ username?: string }>(token, "getMe").then((m) => m.username ?? null).catch(() => null);
    hooked = await telegram<{ url?: string }>(token, "getWebhookInfo")
      .then((w) => Boolean(w.url?.endsWith("/api/vendas/telegram")))
      .catch(() => false);
  }
  const config = await telegramConfig();
  const [products, media, fans, orders] = await Promise.all([
    dbSelect<ProductRow>("sales_products", "select=*&order=position.asc"),
    dbSelect<ProductMediaRow>("sales_product_media", "select=product_id,media_id,position,kind&order=position.asc"),
    dbSelect<FanRow & { started_at: string }>("fans", "select=id,telegram_id,adult_confirmed_at,opted_out_at,blocked_at,started_at"),
    dbSelect<{ id: string; status: string; amount_cents: number; created_at: string; product_id: string }>(
      "sales_orders",
      "select=id,status,amount_cents,created_at,product_id&order=created_at.desc&limit=50"
    ),
  ]);
  const ids = [...new Set(media.map((m) => m.media_id))];
  const paths = ids.length ? await dbSelect<{ id: string; local_path: string }>("media_assets", `select=id,local_path&id=in.(${ids.join(",")})`) : [];
  const pathOf = new Map(paths.map((m) => [m.id, m.local_path]));
  const channel = await telegramChannel().catch(() => null);
  return {
    storeReady: storeConfigured(),
    tokenSet: Boolean(token),
    bot,
    connected: hooked,
    adminStarted: Boolean(config.chatId && fans.some((f) => String(f.telegram_id) === config.chatId)),
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
    orders,
  };
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
      if (body.period_days !== undefined && p.kind === "vip") {
        const days = Number(body.period_days);
        if (!Number.isInteger(days) || days < 1 || days > 366) throw new Problem(400, "Prazo do VIP entre 1 e 366 dias.");
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
      const chat = await adminChat();
      const video = path.endsWith(".mp4");
      try {
        const url = await storageReadUrl(UPLOAD_BUCKET, path, 600);
        const sent = await telegram<{ message_id: number; photo?: { file_id: string }[]; video?: { file_id: string } }>(needToken(), video ? "sendVideo" : "sendPhoto", {
          chat_id: chat,
          [video ? "video" : "photo"]: url,
          caption: `Guardado no pacote “${p.title}”.`,
          disable_notification: true,
        });
        const fileId = video ? sent.video?.file_id : sent.photo?.at(-1)?.file_id;
        if (!fileId) throw new Problem(502, "O Telegram não devolveu o arquivo.");
        await telegram(needToken(), "deleteMessage", { chat_id: chat, message_id: sent.message_id }).catch(() => undefined);
        await dbDelete("sales_product_media", `product_id=eq.${p.id}&media_id=eq.${mediaId}`);
        await dbInsert("sales_product_media", {
          product_id: p.id,
          media_id: mediaId,
          position: current.filter((m) => m.media_id !== mediaId).length,
          kind: video ? "video" : "photo",
          telegram_file_id: fileId,
        });
      } finally {
        await storageRemove(UPLOAD_BUCKET, [path]).catch(() => undefined);
      }
      await audit("pacote_midia", "sales_products", p.id, { media_id: mediaId });
    } else if (body.action === "media-remove") {
      const p = await getProduct(body.product_id);
      await dbDelete("sales_product_media", `product_id=eq.${p.id}&media_id=eq.${uuid(body.media_id, "Mídia")}`);
      await audit("pacote_tirar_midia", "sales_products", p.id, { media_id: body.media_id });
    } else if (body.action === "test") {
      await sendMenu(needToken(), await adminChat());
    } else {
      throw new Problem(400, "Ação desconhecida.");
    }
    return reply(200, await status());
  } catch (e) {
    if (e instanceof Problem) return reply(e.status, { detail: e.message });
    return reply(500, { detail: e instanceof Error ? e.message : String(e) });
  }
}

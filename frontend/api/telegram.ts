import type { IncomingMessage, ServerResponse } from "http";
import { isAuthenticated } from "./_auth.js";
import { storeConfigured } from "./_store.js";
import {
  forgetTelegram,
  looksLikeToken,
  saveChat,
  saveMedia,
  saveToken,
  sendNotice,
  telegram,
  telegramConfig,
} from "./_telegram.js";

interface BotInfo {
  username?: string;
}

interface Update {
  message?: { chat?: { id?: number; type?: string; first_name?: string } };
}

// /api/telegram - configura o aviso no celular (pagina "Meu perfil").
// GET: estado (sem nunca devolver o token). POST { action }:
//   token { token }  - confere com o Telegram (getMe) e guarda
//   connect          - acha a conversa de quem mandou /start para o bot
//   media { value }  - mandar a foto/video junto ou so o texto
//   test             - manda uma mensagem de teste
//   forget           - apaga token e conversa
export default async function handler(req: IncomingMessage, res: ServerResponse) {
  res.setHeader("Content-Type", "application/json");
  const reply = (status: number, body: object) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (!isAuthenticated(req)) return reply(401, { detail: "Sessao expirada. Entre de novo." });

  const status = async () => {
    const config = await telegramConfig();
    let bot: string | null = null;
    if (config.token) {
      bot = await telegram<BotInfo>(config.token, "getMe")
        .then((me) => me.username ?? null)
        .catch(() => null);
    }
    return {
      storeReady: storeConfigured(),
      tokenSet: Boolean(config.token),
      bot,
      connected: Boolean(config.token && config.chatId),
      media: config.media,
    };
  };

  if (req.method === "GET") return reply(200, await status());
  if (req.method !== "POST") return reply(405, { detail: "Use GET ou POST." });
  if (!storeConfigured()) {
    return reply(501, { detail: "Falta conectar o banco Upstash Redis ao projeto na Vercel (aba Storage)." });
  }

  let raw = "";
  for await (const chunk of req) raw += chunk;
  let body: { action?: string; token?: unknown; value?: unknown };
  try {
    body = JSON.parse(raw || "{}");
  } catch {
    return reply(400, { detail: "JSON invalido." });
  }

  try {
    if (body.action === "token") {
      const token = typeof body.token === "string" ? body.token.trim() : "";
      if (!looksLikeToken(token)) {
        return reply(400, { detail: "Isso nao parece um token de bot (formato 123456789:ABC...)." });
      }
      try {
        await telegram<BotInfo>(token, "getMe");
      } catch {
        return reply(400, { detail: "O Telegram nao aceitou esse token. Copie de novo do @BotFather." });
      }
      await saveToken(token);
    } else if (body.action === "connect") {
      const config = await telegramConfig();
      if (!config.token) return reply(400, { detail: "Cole o token do bot primeiro." });
      const updates = await telegram<Update[]>(config.token, "getUpdates", { limit: 100 });
      const chat = updates
        .map((u) => u.message?.chat)
        .filter((c) => c?.id && c.type === "private")
        .pop();
      if (!chat?.id) {
        return reply(400, { detail: "Ainda nao recebi sua mensagem: abra o bot no Telegram, toque em Começar e tente de novo." });
      }
      await saveChat(String(chat.id));
      await sendNotice(
        { ...config, chatId: String(chat.id) },
        "Pronto! Vou te avisar aqui quando as fotos e os vídeos do estúdio ficarem prontos."
      );
    } else if (body.action === "media") {
      await saveMedia(body.value === true);
    } else if (body.action === "test") {
      await sendNotice(await telegramConfig(), "Teste do Luna Studio: os avisos estão funcionando.");
    } else if (body.action === "forget") {
      await forgetTelegram();
    } else {
      return reply(400, { detail: "Acao desconhecida." });
    }
  } catch (e) {
    return reply(502, { detail: e instanceof Error ? e.message : String(e) });
  }
  return reply(200, await status());
}

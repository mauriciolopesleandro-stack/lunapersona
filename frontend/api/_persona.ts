import { AsyncLocalStorage } from "node:async_hooks";
import type { IncomingMessage } from "http";

// Varias personas no mesmo estudio (10/11/2026: Luna e Cleuza).
//
// Cada pedido que chega no estudio diz de qual persona e (?p=cleuza, ou o
// cabecalho X-Persona que o estudio manda); sem nada, e a Luna - tudo o que ja
// existia continua igual. A persona fica guardada durante o pedido inteiro
// (AsyncLocalStorage), e as pecas centrais separam os dados sozinhas:
//   _store.ts     chaves "luna:..." viram "cleuza:..." (as compartilhadas, como
//                 o Pix e o login, ficam iguais para as duas)
//   _supabase.ts  as tabelas de conteudo/publicacao/venda filtram e gravam a
//                 coluna persona (migracao 0010)
// Processos que nao vem de um pedido (fila de tarefas, cron) rodam cada tarefa
// dentro da persona dela com runAs().

export interface Persona {
  id: string; // vai no banco e nas chaves (so a-z, 0-9 e -)
  name: string; // nome que aparece para o publico
  clone: string; // nome do clone na Naiaflow
  hair: string; // cabelo do clone (vai na frase do rosto quando ha foto de referencia)
  brief: string; // ficha da persona (aprovada pelo administrador) - vai para as regras das IAs numa fase seguinte
}

export const PERSONAS: Record<string, Persona> = {
  luna: { id: "luna", name: "Luna", clone: "luna", hair: "castanho escuro, longo", brief: "" }, // a Luna segue com os textos de sempre (_assistant.ts)
  cleuza: {
    id: "cleuza",
    name: "Cleuza",
    clone: "Cleuza",
    hair: "castanho escuro, longo e ondulado",
    // ficha ditada pelo administrador por audio em 10/11/2026
    brief: [
      "Cleuza é uma coroa: mulher madura, mais velha, e orgulhosa disso. Retrato de uma mulher mais velha de verdade, nada de pose fitness.",
      "Nordestina, com o jeito de falar do Nordeste.",
      "Caseira: vive em casa, adora churrasco e cerveja. Não faz academia; corpo natural de mulher madura.",
      "Fala muita besteira: desbocada, piadista, sem filtro, bem-humorada e provocante.",
      "Bordões: \"quero ver você rejeitar uma coroa dessa\", \"uma coroa dessa é melhor que muitas novinhas\", \"esse novinho eu não recuso\", \"panela velha é que faz comida boa\".",
      "Público: homens mais novos (os novinhos) que curtem mulher madura.",
      "Posta vídeos falando para a câmera nesse tom.",
      "Cenários: churrasco no quintal, cerveja na mão, cozinha, sofá de casa, varanda, laje - nada de academia.",
    ].join("\n"),
  },
};

export const DEFAULT_PERSONA = "luna";

const current = new AsyncLocalStorage<string>();

export function personaId(): string {
  return current.getStore() ?? DEFAULT_PERSONA;
}

export function persona(): Persona {
  return PERSONAS[personaId()] ?? PERSONAS[DEFAULT_PERSONA];
}

export function isPersona(id: unknown): id is string {
  return typeof id === "string" && Object.prototype.hasOwnProperty.call(PERSONAS, id);
}

// Roda fn dentro da persona (persona desconhecida ou vazia: Luna)
export function runAs<T>(id: string | null | undefined, fn: () => T): T {
  return current.run(isPersona(id) ? id : DEFAULT_PERSONA, fn);
}

// A persona pedida no pedido HTTP: ?p= (webhooks, links assinados) ou o
// cabecalho X-Persona (estudio). Valor desconhecido: null (a rota recusa).
export function requestPersona(req: IncomingMessage): string | null {
  const url = new URL(req.url ?? "/", "http://local");
  const raw = url.searchParams.get("p") ?? (req.headers["x-persona"] as string | undefined) ?? "";
  const id = raw.trim().toLowerCase();
  if (!id) return DEFAULT_PERSONA;
  return isPersona(id) ? id : null;
}

// Chaves do armazenamento (Upstash) que valem para todas as personas: login,
// sessao, trava de senha errada e o Pix (mesma conta PushinPay, pedido de 10/11).
// O robo do PC e um so (11/11): a chave dele, quando apareceu e as roupas da
// galeria da Naiaflow tambem valem para todas.
const SHARED_KEYS = [/^luna:login_password$/, /^luna:session_version$/, /^luna:auth_fail:/, /^luna:pix-token$/, /^luna:pix-env$/, /^luna:runpod-/, /^luna:worker-gen$/, /^luna:worker-seen$/, /^luna:naia-outfits$/];

// Enderecos do proprio site que o Telegram/PushinPay chamam de volta (webhooks,
// aviso do Pix): levam a persona (?p=cleuza) para o pedido voltar na persona
// certa. Luna: sem nada, como sempre (os webhooks dela continuam iguais).
export function personaParam(sep: "?" | "&"): string {
  const id = personaId();
  return id === DEFAULT_PERSONA ? "" : `${sep}p=${encodeURIComponent(id)}`;
}

// Webhooks dos bots: a persona vai no caminho (/api/telegram/webhook/cleuza),
// com regra propria no vercel.json - nao depende de a Vercel repassar o ?p=.
export function personaPath(): string {
  const id = personaId();
  return id === DEFAULT_PERSONA ? "" : `/${encodeURIComponent(id)}`;
}

export function personaKey(key: string): string {
  const id = personaId();
  if (id === DEFAULT_PERSONA || !key.startsWith("luna:") || SHARED_KEYS.some((re) => re.test(key))) return key;
  return `${id}:${key.slice("luna:".length)}`;
}

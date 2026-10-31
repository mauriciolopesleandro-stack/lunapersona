import { storeConfigured, storeGet, storeSet } from "./_store.js";

// Memoria da Conversa: o que o administrador ensina falando ("ficou ruim",
// "nao gosto de...", "sempre faca..."). A IA termina a resposta com a linha
// "APRENDI: <regra>" e o servidor guarda a regra; todas as regras entram no
// contexto das proximas conversas (no painel e, depois, no Telegram), e a IA
// passa a seguir. "ESQUECI: <numero>" tira uma regra. Diferente das licoes de
// _lessons.ts (a IA olhando as fotos geradas): aqui e o gosto do administrador.

const KEY = "luna:memoria-conversa";
const MAX_RULES = 60;
const MAX_CHARS = 300;

export interface Rule {
  at: string;
  rule: string;
}

export async function listRules(): Promise<Rule[]> {
  if (!storeConfigured()) return [];
  try {
    const list = JSON.parse((await storeGet(KEY)) ?? "[]");
    return Array.isArray(list) ? list.filter((r) => typeof r?.rule === "string") : [];
  } catch {
    return [];
  }
}

async function saveRules(list: Rule[]): Promise<void> {
  if (storeConfigured()) await storeSet(KEY, JSON.stringify(list.slice(0, MAX_RULES)));
}

const norm = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

// guarda as regras novas (a mais recente primeiro; repetida nao entra de novo)
export async function addRules(rules: string[]): Promise<Rule[]> {
  const list = await listRules();
  const added: Rule[] = [];
  for (const raw of rules) {
    const rule = raw.trim().slice(0, MAX_CHARS);
    if (rule.length < 8 || list.some((r) => norm(r.rule) === norm(rule))) continue;
    const r = { at: new Date().toISOString(), rule };
    list.unshift(r);
    added.push(r);
  }
  if (added.length) await saveRules(list);
  return added;
}

// tira regras pelo numero que aparece no contexto (1 = a mais recente); "all" apaga tudo
export async function forgetRules(which: number[] | "all"): Promise<Rule[]> {
  const list = await listRules();
  const keep = which === "all" ? [] : list.filter((_, i) => !which.includes(i + 1));
  await saveRules(keep);
  return keep;
}

// Para o prompt de sistema: as regras numeradas (o numero e o que o ESQUECI usa)
export async function rulesForChat(): Promise<string> {
  const list = await listRules();
  if (!list.length) return "";
  return [
    "O QUE O ADMINISTRADOR JÁ TE ENSINOU (siga sempre; valem mais que as regras gerais de estilo, nunca mais que as proibições; a mais recente primeiro):",
    ...list.map((r, i) => `${i + 1}. ${r.rule}`),
  ].join("\n");
}

// Tira da resposta da IA as linhas APRENDI/ESQUECI e devolve o que pediram
export function extractMemory(text: string): { reply: string; learn: string[]; forget: number[] | "all" | null } {
  const learn: string[] = [];
  let forget: number[] | "all" | null = null;
  const kept: string[] = [];
  for (const line of text.split("\n")) {
    const clean = line.replace(/\*\*|__|`/g, "").replace(/^[^\p{L}]+/u, "").trim();
    const a = clean.match(/^APRENDI\s*[:：\-–]\s*(.+)$/i);
    const f = clean.match(/^ESQUECI\s*[:：\-–]\s*(.+)$/i);
    if (a) learn.push(a[1].replace(/^["“]|["”]$/g, ""));
    else if (f) {
      if (/tud|todas/i.test(f[1])) forget = "all";
      else {
        const nums = (f[1].match(/\d+/g) ?? []).map(Number).filter((n) => n > 0);
        if (nums.length && forget !== "all") forget = [...(forget ?? []), ...nums];
      }
    } else kept.push(line);
  }
  return { reply: kept.join("\n").trim(), learn, forget };
}

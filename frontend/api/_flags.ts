// Flags neutras de uma publicacao e a traducao para cada rede. Modulo puro (sem
// Node): o servidor usa ao salvar/publicar e o compositor (src/studio) importa
// o mesmo arquivo para mostrar, rede a rede, o que vai automatico e o que fica
// para fazer no app. Fonte unica: mudou uma regra aqui, muda nos dois lados.

export type FlagNetwork = "instagram" | "tiktok" | "telegram";

export interface PostFlags {
  aiGenerated?: boolean; // conteudo gerado ou editado com IA
  music?: string; // musica sugerida (nome - artista)
  commentsOff?: boolean; // desligar comentarios
  coverIndex?: number; // posicao da midia de capa (0 = primeira)
  aspect?: Aspect; // recorte das fotos (centro), feito no navegador antes de enviar
  location?: string; // localizacao (texto)
}

export type Aspect = "1:1" | "4:5" | "9:16";
export const ASPECTS: Aspect[] = ["1:1", "4:5", "9:16"];
export function aspectRatio(a: Aspect): number {
  const [w, h] = a.split(":").map(Number);
  return w / h;
}

export const FLAG_LABELS: Record<keyof PostFlags, string> = {
  aiGenerated: "Feito com IA",
  music: "Música",
  commentsOff: "Comentários desligados",
  coverIndex: "Capa",
  aspect: "Enquadramento",
  location: "Localização",
};

// Le e valida o que veio do navegador; guarda so o que esta ligado (assim uma
// publicacao sem flags continua com {} e a impressao digital antiga).
export function normalizeFlags(raw: unknown, mediaCount: number): PostFlags {
  const r = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const out: PostFlags = {};
  if (r.aiGenerated === true) out.aiGenerated = true;
  if (typeof r.music === "string" && r.music.trim()) out.music = r.music.trim().slice(0, 120);
  if (r.commentsOff === true) out.commentsOff = true;
  if (typeof r.coverIndex === "number" && Number.isInteger(r.coverIndex) && r.coverIndex > 0 && r.coverIndex < mediaCount) {
    out.coverIndex = r.coverIndex;
  }
  if (typeof r.aspect === "string" && (ASPECTS as string[]).includes(r.aspect)) out.aspect = r.aspect as Aspect;
  if (typeof r.location === "string" && r.location.trim()) out.location = r.location.trim().slice(0, 100);
  return out;
}

// Combinacoes que a rede recusaria (bloqueiam salvar). null = tudo certo.
export function flagProblem(network: FlagNetwork, formats: string[], flags: PostFlags): string | null {
  if (network === "instagram" && formats.includes("Feed") && flags.aspect === "9:16") {
    return "O Feed do Instagram aceita fotos de 4:5 até 1,91:1: use 4:5 ou 1:1 no Feed (9:16 serve para Stories e Reels).";
  }
  return null;
}

// Texto canonico (chaves em ordem) para a impressao digital da aprovacao.
export function canonicalFlags(flags: PostFlags): string {
  return JSON.stringify(Object.keys(flags).sort().map((k) => [k, flags[k as keyof PostFlags]]));
}

export interface FlagTranslation {
  flag: keyof PostFlags;
  label: string;
  how: "automatico" | "no_app";
  note: string;
}

// Como cada flag ligada vira realidade numa rede (formato e modo considerados).
export function translateFlags(network: FlagNetwork, formats: string[], mode: "manual" | "api", flags: PostFlags): FlagTranslation[] {
  const out: FlagTranslation[] = [];
  const auto = mode === "api";
  const tiktokPhotos = network === "tiktok" && formats.includes("Foto");
  const add = (flag: keyof PostFlags, how: FlagTranslation["how"], note: string) => out.push({ flag, label: FLAG_LABELS[flag], how, note });

  if (flags.aiGenerated) {
    if (auto && tiktokPhotos) add("aiGenerated", "automatico", "vai com o rótulo de conteúdo gerado por IA do TikTok");
    else if (network === "tiktok") add("aiGenerated", "no_app", "ao publicar, ative “Conteúdo gerado por IA” nas opções do TikTok");
    else if (network === "instagram") add("aiGenerated", "no_app", "a API do Instagram não marca IA: ative o rótulo “Info de IA” no app");
    else add("aiGenerated", "no_app", "o Telegram não tem rótulo de IA: avise na legenda se quiser");
  }
  if (flags.music) {
    if (network === "telegram") add("music", "no_app", `o Telegram não tem trilha: cite “${flags.music}” na legenda se quiser`);
    else add("music", "no_app", `as APIs não colocam música: adicione “${flags.music}” no app antes de publicar`);
  }
  if (flags.commentsOff) {
    if (network === "instagram") add("commentsOff", "no_app", "a API não desliga comentários: desligue no menu do post depois de publicado");
    else if (network === "tiktok") add("commentsOff", "no_app", "desligue “Permitir comentários” ao publicar no app");
    else add("commentsOff", "no_app", "no Telegram, comentários dependem do grupo de discussão do canal");
  }
  if (flags.location) {
    if (network === "instagram") add("location", "no_app", `adicione “${flags.location}” no app (pela API a localização exige uma permissão extra da Meta)`);
    else if (network === "tiktok") add("location", "no_app", `adicione “${flags.location}” ao publicar no app`);
    else add("location", "no_app", `cite “${flags.location}” na legenda se quiser`);
  }
  if (flags.aspect) {
    if (auto) add("aspect", "automatico", `as fotos são recortadas no centro em ${flags.aspect} antes de enviar`);
    else add("aspect", "no_app", `recorte as fotos em ${flags.aspect} no app ao publicar`);
  }
  if (flags.coverIndex) {
    const n = flags.coverIndex + 1;
    if (auto && tiktokPhotos) add("coverIndex", "automatico", `a foto ${n} vai como capa`);
    else add("coverIndex", "no_app", `a capa é a primeira mídia: para a foto ${n} ser a capa, coloque-a em primeiro ou troque no app`);
  }
  return out;
}

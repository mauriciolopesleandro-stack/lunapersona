// Testa a migracao 0004_tiktok_rascunho (depois da 0001 a 0003) num Postgres
// real (PGlite), com um esboco do schema storage do Supabase.
import { PGlite } from "@electric-sql/pglite";
import fs from "node:fs";

const read = (name) => fs.readFileSync(new URL(`../migrations/${name}`, import.meta.url), "utf8");
const db = new PGlite();
await db.exec(`
  create role anon; create role authenticated; create role service_role bypassrls;
  alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  grant usage on schema public to anon, authenticated, service_role;
  create schema storage;
  create table storage.buckets (id text primary key, name text not null, public boolean default false,
    file_size_limit bigint, allowed_mime_types text[]);
`);
// rodar duas vezes: as migracoes podem ser repetidas
for (let i = 0; i < 2; i++) {
  for (const m of ["0001_fundacao.sql", "0002_publicacoes.sql", "0003_instagram_automatico.sql", "0004_tiktok_rascunho.sql"]) await db.exec(read(m));
}

let fails = 0;
const report = (label, pass, msg = "") => {
  if (!pass) fails++;
  console.log(`${pass ? "OK  " : "FALHA"} ${label}${msg ? ` -> ${msg}` : ""}`);
};
const expect = async (label, q, shouldWork) => {
  try {
    await db.query(q);
    report(label, shouldWork);
  } catch (e) {
    report(label, !shouldWork, e.message);
  }
};

const [post] = (await db.query(`insert into posts (network, content_hash, publish_mode) values ('tiktok', '${"b".repeat(64)}', 'api') returning id`)).rows;
const P = post.id;
const job = (kind, key) => `insert into scheduled_jobs (kind, post_id, post_version, run_at, idempotency_key) values ('${kind}', '${P}', 1, now(), '${key}')`;
await expect("tarefa enviar_tiktok aceita", job("enviar_tiktok", "tiktok:a"), true);
await expect("tarefas anteriores continuam aceitas (lembrete)", job("lembrete_publicacao", "lembrete:a"), true);
await expect("tarefas anteriores continuam aceitas (instagram)", job("publicar_instagram", "publicar:a"), true);
await expect("tarefa de tipo desconhecido recusada", job("apagar_tudo", "x"), false);
await expect("tentativa pela API do TikTok registrada", `insert into publishing_attempts (post_id, channel, outcome) values ('${P}', 'tiktok_api', 'ok')`, true);
await expect("canais anteriores continuam aceitos", `insert into publishing_attempts (post_id, channel, outcome) values ('${P}', 'instagram_api', 'ok')`, true);
await expect("canal desconhecido recusado", `insert into publishing_attempts (post_id, channel, outcome) values ('${P}', 'whatsapp', 'ok')`, false);
await expect("publish_id do TikTok no destino", `insert into post_destinations (post_id, format, external_id) values ('${P}', 'Vídeo', 'v_inbox_file~v2.123')`, true);
console.log(fails ? `${fails} FALHA(S)` : "TUDO OK");
process.exit(fails ? 1 : 0);

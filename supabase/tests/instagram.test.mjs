// Testa a migracao 0003_instagram_automatico (depois da 0001 e 0002) num
// Postgres real (PGlite), com um esboco do schema storage do Supabase.
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
for (let i = 0; i < 2; i++) {
  for (const m of ["0001_fundacao.sql", "0002_publicacoes.sql", "0003_instagram_automatico.sql"]) await db.exec(read(m));
}

let fails = 0;
const report = (label, pass, msg = "") => {
  if (!pass) fails++;
  console.log(`${pass ? "OK  " : "FALHA"} ${label}${msg ? ` -> ${msg}` : ""}`);
};
async function as(role, q) {
  await db.exec(`set role ${role}`);
  try {
    return (await db.query(q)).rows;
  } finally {
    await db.exec("reset role");
  }
}
const expect = async (label, role, q, shouldWork) => {
  try {
    await as(role, q);
    report(label, shouldWork);
  } catch (e) {
    report(label, !shouldWork, e.message);
  }
};

const bucket = (await db.query("select public, file_size_limit, allowed_mime_types from storage.buckets where id='publicar-temp'")).rows[0];
report("bucket temporario privado, 50 MB, so JPEG/MP4/MOV", bucket && !bucket.public && Number(bucket.file_size_limit) === 52428800 && bucket.allowed_mime_types.join() === "image/jpeg,video/mp4,video/quicktime", JSON.stringify(bucket));
const [post] = await as("service_role", `insert into posts (network, content_hash, publish_mode) values ('instagram', '${"a".repeat(64)}', 'api') returning id`);
const P = post.id;
await expect("copia temporaria registrada", "service_role", `insert into post_uploads (post_id, post_version, position, storage_path, mime_type) values ('${P}', 1, 0, '${P}/v1/0.jpg', 'image/jpeg')`, true);
await expect("mesma posicao na mesma versao recusada", "service_role", `insert into post_uploads (post_id, post_version, position, storage_path, mime_type) values ('${P}', 1, 0, '${P}/v1/0b.jpg', 'image/jpeg')`, false);
await expect("formato fora do aceito recusado (PNG)", "service_role", `insert into post_uploads (post_id, post_version, position, storage_path, mime_type) values ('${P}', 1, 1, '${P}/v1/1.png', 'image/png')`, false);
await expect("copia nao se apaga (so marca deleted_at)", "service_role", "delete from post_uploads", false);
await expect("anon nao le as copias", "anon", "select * from post_uploads", false);
await expect("tarefa publicar_instagram aceita", "service_role", `insert into scheduled_jobs (kind, post_id, post_version, run_at, idempotency_key) values ('publicar_instagram', '${P}', 1, now(), 'publicar:x')`, true);
await expect("tarefa de tipo desconhecido recusada", "service_role", `insert into scheduled_jobs (kind, post_id, post_version, run_at, idempotency_key) values ('apagar_tudo', '${P}', 1, now(), 'y')`, false);
await expect("tentativa pela API do Instagram registrada", "service_role", `insert into publishing_attempts (post_id, channel, outcome) values ('${P}', 'instagram_api', 'ok')`, true);
await expect("conteiner do Instagram no destino", "service_role", `insert into post_destinations (post_id, format, external_container_id) values ('${P}', 'Feed', '1789')`, true);
const cols = (await db.query("select column_name from information_schema.columns where table_name='platform_connections' and column_name in ('external_user_id','token_refreshed_at')")).rows.length;
report("conexao guarda conta e data de renovacao", cols === 2);
console.log(fails ? `${fails} FALHA(S)` : "TUDO OK");
process.exit(fails ? 1 : 0);

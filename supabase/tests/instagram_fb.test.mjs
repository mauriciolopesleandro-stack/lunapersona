// Testa a migracao 0006_instagram_facebook (depois da 0001 a 0005) num
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
for (const m of ["0001_fundacao.sql", "0002_publicacoes.sql", "0003_instagram_automatico.sql", "0004_tiktok_rascunho.sql", "0005_grupos_e_flags.sql"]) {
  await db.exec(read(m));
}
// conexao antiga, de antes da 0006
const [acc] = (await db.query(`insert into social_accounts (network, handle) values ('instagram', 'luna') returning id`)).rows;
await db.query(`insert into platform_connections (account_id, mode, status, token_ciphertext) values ('${acc.id}', 'api', 'conectado', 'v1:a:b:c')`);
// rodar duas vezes: a migracao pode ser repetida
for (let i = 0; i < 2; i++) await db.exec(read("0006_instagram_facebook.sql"));

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

const old = (await db.query(`select provider, backup_ciphertext, token_ciphertext from platform_connections`)).rows[0];
report("conexao antiga continua igual (provider nulo, token intacto)", old.provider === null && old.backup_ciphertext === null && old.token_ciphertext === "v1:a:b:c", JSON.stringify(old));
await expect("trocar para facebook_login guardando o backup", `update platform_connections set provider='facebook_login', backup_ciphertext=token_ciphertext, backup_user_id='1', token_ciphertext='v1:x:y:z'`, true);
await expect("provider desconhecido recusado", `update platform_connections set provider='senha_no_chat'`, false);
await expect("voltar para instagram_login", `update platform_connections set provider='instagram_login', token_ciphertext=backup_ciphertext, backup_ciphertext=null`, true);
console.log(fails ? `${fails} FALHA(S)` : "TUDO OK");
process.exit(fails ? 1 : 0);

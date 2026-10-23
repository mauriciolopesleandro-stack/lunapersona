// Testa a migracao 0005_grupos_e_flags (depois da 0001 a 0004) num Postgres
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
const all = ["0001_fundacao.sql", "0002_publicacoes.sql", "0003_instagram_automatico.sql", "0004_tiktok_rascunho.sql"];
for (const m of all) await db.exec(read(m));
// publicacao antiga, de antes da 0005
const [old] = (await db.query(`insert into posts (network, content_hash) values ('instagram', '${"c".repeat(64)}') returning id`)).rows;
// rodar duas vezes: a migracao pode ser repetida
for (let i = 0; i < 2; i++) await db.exec(read("0005_grupos_e_flags.sql"));

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

const before = (await db.query(`select group_id, flags from posts where id='${old.id}'`)).rows[0];
report("publicacao antiga fica sem grupo e com flags vazias", before.group_id === null && JSON.stringify(before.flags) === "{}", JSON.stringify(before));
const g = "11111111-1111-4111-8111-111111111111";
await expect("duas redes no mesmo grupo", `insert into posts (network, content_hash, group_id, flags) values ('instagram', '${"d".repeat(64)}', '${g}', '{"aiGenerated": true}'), ('tiktok', '${"e".repeat(64)}', '${g}', '{"aiGenerated": true}')`, true);
report("grupo junta as duas", (await db.query(`select count(*)::int as n from posts where group_id='${g}'`)).rows[0].n === 2);
await expect("flags precisam ser objeto (lista recusada)", `insert into posts (network, content_hash, flags) values ('tiktok', '${"f".repeat(64)}', '[1,2]')`, false);
await expect("flags nulas recusadas", `insert into posts (network, content_hash, flags) values ('tiktok', '${"f".repeat(64)}', null)`, false);
report("indice do grupo criado", (await db.query("select 1 from pg_indexes where indexname='posts_group_idx'")).rows.length === 1);
console.log(fails ? `${fails} FALHA(S)` : "TUDO OK");
process.exit(fails ? 1 : 0);

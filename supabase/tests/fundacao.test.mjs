// Testa a migracao 0001_fundacao num Postgres real (PGlite, sem servidor), com papeis e privilegios
// padrao parecidos com os do Supabase. Roda duas vezes e testa as regras.
import { PGlite } from "@electric-sql/pglite";
import fs from "node:fs";

const sql = fs.readFileSync(new URL("../migrations/0001_fundacao.sql", import.meta.url), "utf8");
const db = new PGlite();
await db.exec(`
  create role anon; create role authenticated; create role service_role bypassrls;
  alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  grant usage on schema public to anon, authenticated, service_role;
`);
await db.exec(sql);
await db.exec(sql);
let fails = 0;
const expect = async (label, role, q, shouldWork) => {
  let ok = true, msg = "";
  try {
    await db.exec(`set role ${role}`);
    await db.query(q);
  } catch (e) {
    ok = false;
    msg = e.message;
  } finally {
    await db.exec("reset role");
  }
  const pass = ok === shouldWork;
  if (!pass) fails++;
  console.log(`${pass ? "OK  " : "FALHA"} ${label}${msg ? ` -> ${msg}` : ""}`);
};
const luna = (await db.query("select id from characters where name='Luna Vox'")).rows[0]?.id;
console.log("personagem padrao:", luna ? "Luna Vox" : "FALTANDO");
if (!luna) fails++;
const ins = (extra = "") =>
  `insert into media_assets (local_path, kind, mime_type, size_bytes, title${extra ? ", " + extra.split("=")[0] : ""}) values ('a${Math.random()}/x.jpg','image','image/jpeg',10,'X'${extra ? ", " + extra.split("=")[1] : ""})`;
await expect("service_role insere conteudo", "service_role", ins(), true);
await expect("service_role atualiza", "service_role", "update media_assets set status='aprovado'", true);
await expect("service_role NAO apaga conteudo", "service_role", "delete from media_assets", false);
await expect("anon NAO le", "anon", "select * from media_assets", false);
await expect("authenticated NAO le", "authenticated", "select * from media_assets", false);
await expect("adulto + instagram recusado", "service_role", ins("content_rating,allowed_destinations=('adulto'), array['instagram']").replace("('adulto'), array", "'adulto', array"), false);
await expect("adulto + telegram aceito", "service_role", ins("content_rating,allowed_destinations='adulto', array['telegram']").replace("=", ""), true);
await expect("destino invalido recusado", "service_role", ins("allowed_destinations=array['onlyfans']"), false);
await expect("status invalido recusado", "service_role", "update media_assets set status='apagado'", false);
await expect("sha256 invalido recusado", "service_role", ins("sha256='abc'"), false);
await expect("caminho repetido recusado", "service_role",
  "insert into media_assets (local_path, kind, mime_type, size_bytes, title) values ('dup.jpg','image','image/jpeg',1,'a'),('dup.jpg','image','image/jpeg',1,'b')", false);
await expect("campanha com fim antes do inicio recusada", "service_role",
  "insert into campaigns (name, starts_on, ends_on) values ('C', '2026-10-10', '2026-10-01')", false);
await expect("service_role grava uso", "service_role",
  "insert into media_usage (media_id, channel, context) select id, 'instagram', 'post' from media_assets limit 1", true);
await expect("uso NAO e alterado", "service_role", "update media_usage set note='x'", false);
await expect("service_role grava auditoria", "service_role", "insert into audit_logs (action, entity) values ('teste','media')", true);
await expect("auditoria NAO e apagada", "service_role", "delete from audit_logs", false);
const before = (await db.query("select updated_at from characters where name='Luna Vox'")).rows[0].updated_at;
await new Promise((r) => setTimeout(r, 20));
await db.query("update characters set description='nova' where name='Luna Vox'");
const after = (await db.query("select updated_at from characters where name='Luna Vox'")).rows[0].updated_at;
const trig = after > before;
if (!trig) fails++;
console.log(`${trig ? "OK  " : "FALHA"} updated_at automatico`);
const rls = (await db.query("select relname from pg_class where relname in ('characters','campaigns','media_assets','media_usage','audit_logs') and relrowsecurity")).rows.length;
if (rls !== 5) fails++;
console.log(`${rls === 5 ? "OK  " : "FALHA"} RLS ligado em ${rls}/5 tabelas`);
console.log(fails ? `${fails} FALHA(S)` : "TUDO OK");
process.exit(fails ? 1 : 0);

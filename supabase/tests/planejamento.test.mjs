// Testa a migracao 0007_planejamento (depois da 0001 a 0006) num Postgres real
// (PGlite), com um esboco do schema storage do Supabase.
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
for (const m of ["0001_fundacao.sql", "0002_publicacoes.sql", "0003_instagram_automatico.sql", "0004_tiktok_rascunho.sql", "0005_grupos_e_flags.sql", "0006_instagram_facebook.sql"]) {
  await db.exec(read(m));
}
// rodar duas vezes: a migracao pode ser repetida
for (let i = 0; i < 2; i++) await db.exec(read("0007_planejamento.sql"));

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

const [plan] = (await db.query(`insert into content_plans (title) values ('Semana 1') returning id`)).rows;
const when = "now() + interval '1 day'";
await expect("item de Feed", `insert into content_plan_items (plan_id, position, scheduled_at, network, format, prompt) values ('${plan.id}', 0, ${when}, 'instagram', 'Feed', 'Luna na praia')`, true);
await expect("+18 no Telegram", `insert into content_plan_items (plan_id, position, scheduled_at, network, format, adult) values ('${plan.id}', 1, ${when}, 'telegram', 'Canal', true)`, true);
await expect("+18 no Instagram e recusado", `insert into content_plan_items (plan_id, position, scheduled_at, network, format, adult) values ('${plan.id}', 2, ${when}, 'instagram', 'Feed', true)`, false);
await expect("+18 no TikTok e recusado", `insert into content_plan_items (plan_id, position, scheduled_at, network, format, adult) values ('${plan.id}', 2, ${when}, 'tiktok', 'Foto', true)`, false);
await expect("status desconhecido recusado", `insert into content_plan_items (plan_id, position, scheduled_at, network, format, status) values ('${plan.id}', 3, ${when}, 'tiktok', 'Foto', 'publicado_por_magica')`, false);
await expect("posicao repetida no plano recusada", `insert into content_plan_items (plan_id, position, scheduled_at, network, format) values ('${plan.id}', 0, ${when}, 'tiktok', 'Foto')`, false);
const [feed] = (await db.query(`select id from content_plan_items where position = 0`)).rows;
await expect("TikTok reaproveita a foto do Feed", `insert into content_plan_items (plan_id, position, scheduled_at, network, format, source_item_id) values ('${plan.id}', 4, ${when}, 'tiktok', 'Foto', '${feed.id}')`, true);
await expect("apagar o plano apaga os itens", `delete from content_plans where id = '${plan.id}'`, true);
report("itens apagados junto", (await db.query(`select count(*)::int as n from content_plan_items`)).rows[0].n === 0);
console.log(fails ? `${fails} FALHA(S)` : "TUDO OK");
process.exit(fails ? 1 : 0);

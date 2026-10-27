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

// Foto avulsa da Conversa do Assistente (action "quick"): plano ja aprovado
// com 1 item direto na fila do robo.
const [quick] = (await db.query(`insert into content_plans (title, request, status, approved_at) values ('Foto avulsa do chat (Instagram Feed)', '{"source":"chat","slot":"feed"}', 'aprovado', now()) returning id`)).rows;
await expect("foto avulsa entra direto na fila", `insert into content_plan_items (plan_id, position, scheduled_at, network, format, kind, adult, prompt, caption, hashtags, status) values ('${quick.id}', 0, ${when}, 'instagram', 'Feed', 'image', false, 'Luna na praia ao por do sol', 'Fim de tarde', '{#praia}', 'na_fila')`, true);
await expect("foto avulsa +18 vai para o Telegram", `insert into content_plan_items (plan_id, position, scheduled_at, network, format, kind, adult, prompt, status) values ('${quick.id}', 1, ${when}, 'telegram', 'Canal', 'image', true, 'Luna no quarto', 'na_fila')`, true);
report("fila do robo enxerga as fotos avulsas", (await db.query(`select count(*)::int as n from content_plan_items where status = 'na_fila' and kind = 'image' and source_item_id is null`)).rows[0].n === 2);

// Correcao pelo Telegram (parte C): o item ja tem foto e volta para a fila com
// o pedido na nota; o filtro do robo (source_item_id nulo OU media_path
// preenchido) pega tambem a correcao de um item que reaproveitava outra foto.
const queue = `select count(*)::int as n from content_plan_items where status = 'na_fila' and kind = 'image' and (source_item_id is null or media_path is not null)`;
const [src] = (await db.query(`select id from content_plan_items where plan_id = '${quick.id}' and position = 0`)).rows;
await db.query(`update content_plan_items set status = 'em_analise', media_path = 'conteudos/2026-10/a.jpg' where id = '${src.id}'`);
await db.query(`insert into content_plan_items (plan_id, position, scheduled_at, network, format, source_item_id, media_path, status) values ('${quick.id}', 2, ${when}, 'tiktok', 'Foto', '${src.id}', 'conteudos/2026-10/a.jpg', 'em_analise')`);
report("sem correcao: so a foto +18 na fila", (await db.query(queue)).rows[0].n === 1);
await expect("correcao volta para a fila com o pedido", `update content_plan_items set status = 'na_fila', attempts = 0, note = 'Correção: coloca a parte de cima do biquíni' where id = '${src.id}'`, true);
await db.query(`update content_plan_items set status = 'na_fila', note = 'Correção: mais sol' where position = 2 and plan_id = '${quick.id}'`);
report("fila pega as duas correcoes", (await db.query(queue)).rows[0].n === 3);
console.log(fails ? `${fails} FALHA(S)` : "TUDO OK");
process.exit(fails ? 1 : 0);

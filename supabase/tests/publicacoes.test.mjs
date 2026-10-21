// Testa a migracao 0002_publicacoes (depois da 0001) num Postgres real (PGlite),
// com papeis e privilegios padrao parecidos com os do Supabase.
import { PGlite } from "@electric-sql/pglite";
import fs from "node:fs";

const read = (name) => fs.readFileSync(new URL(`../migrations/${name}`, import.meta.url), "utf8");
const db = new PGlite();
await db.exec(`
  create role anon; create role authenticated; create role service_role bypassrls;
  alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  grant usage on schema public to anon, authenticated, service_role;
`);
for (let i = 0; i < 2; i++) {
  await db.exec(read("0001_fundacao.sql"));
  await db.exec(read("0002_publicacoes.sql"));
}

let fails = 0;
const report = (label, pass, msg = "") => {
  if (!pass) fails++;
  console.log(`${pass ? "OK  " : "FALHA"} ${label}${msg ? ` -> ${msg}` : ""}`);
};
async function as(role, q, params) {
  await db.exec(`set role ${role}`);
  try {
    return (await db.query(q, params)).rows;
  } finally {
    await db.exec("reset role");
  }
}
const expect = async (label, role, q, shouldWork, params) => {
  try {
    await as(role, q, params);
    report(label, shouldWork);
  } catch (e) {
    report(label, !shouldWork, e.message);
  }
};
const H1 = "a".repeat(64);
const H2 = "b".repeat(64);

const [post] = await as("service_role", `insert into posts (network, caption, content_hash) values ('instagram', 'oi', '${H1}') returning id`);
const P = post.id;
const [media] = await as(
  "service_role",
  "insert into media_assets (local_path, kind, mime_type, size_bytes, title) values ('2026-10/a.jpg','image','image/jpeg',10,'A') returning id"
);
await expect("midia na publicacao", "service_role", `insert into post_media (post_id, media_id, position) values ('${P}', '${media.id}', 0)`, true);
await expect("mesma midia duas vezes recusada", "service_role", `insert into post_media (post_id, media_id, position) values ('${P}', '${media.id}', 1)`, false);
await expect("agendar sem aprovacao recusado", "service_role", `update posts set status='agendado', scheduled_at=now() where id='${P}'`, false);
await expect("aprovado sem aprovacao recusado", "service_role", `update posts set status='aprovado' where id='${P}'`, false);
await expect(
  "aprovar a versao atual",
  "service_role",
  `update posts set status='aprovado', approved_version=version, approved_hash=content_hash where id='${P}'`,
  true
);
await expect(
  "mudar o conteudo de um aprovado sem voltar para aprovacao recusado",
  "service_role",
  `update posts set version=version+1, content_hash='${H2}' where id='${P}'`,
  false
);
await expect(
  "mudar o conteudo voltando para aguardando aprovacao",
  "service_role",
  `update posts set version=version+1, content_hash='${H2}', status='aguardando_aprovacao' where id='${P}'`,
  true
);
await expect(
  "agendar com a aprovacao antiga recusado",
  "service_role",
  `update posts set status='agendado', scheduled_at=now() where id='${P}'`,
  false
);
await as("service_role", `update posts set status='aprovado', approved_version=version, approved_hash=content_hash where id='${P}'`);
await expect("agendado sem horario recusado", "service_role", `update posts set status='agendado', scheduled_at=null where id='${P}'`, false);
await expect("publicado sem data de publicacao recusado", "service_role", `update posts set status='publicado' where id='${P}'`, false);
await expect("destino com link http recusado", "service_role", `insert into post_destinations (post_id, format, external_url) values ('${P}', 'Feed', 'http://x')`, false);
await expect("destino Feed", "service_role", `insert into post_destinations (post_id, format) values ('${P}', 'Feed')`, true);
await expect("destino repetido recusado", "service_role", `insert into post_destinations (post_id, format) values ('${P}', 'Feed')`, false);
await expect(
  "destino publicado sem confirmacao recusado",
  "service_role",
  `update post_destinations set status='publicado' where post_id='${P}'`,
  false
);
await expect(
  "destino publicado com confirmacao",
  "service_role",
  `update post_destinations set status='publicado', confirmed_at=now(), confirmed_by='admin', external_url='https://instagram.com/p/x' where post_id='${P}'`,
  true
);
await expect("registro de aprovacao", "service_role", `insert into approvals (post_id, version, content_hash, decision) values ('${P}', 2, '${H2}', 'aprovado')`, true);
await expect("aprovacao nao se altera", "service_role", "update approvals set decision='reprovado'", false);
await expect("publicacao nao se apaga", "service_role", "delete from posts", false);
await expect("lista de midias pode ser trocada", "service_role", `delete from post_media where post_id='${P}'`, true);
await expect("anon nao le publicacoes", "anon", "select * from posts", false);
await expect("authenticated nao le a fila", "authenticated", "select * from scheduled_jobs", false);

// fila
const job = (key, when) =>
  as("service_role", `insert into scheduled_jobs (kind, post_id, post_version, run_at, idempotency_key) values ('lembrete_publicacao', '${P}', 2, ${when}, '${key}')`);
await job("k1", "now() - interval '1 minute'");
await job("k2", "now() - interval '2 minutes'");
await job("k3", "now() + interval '1 hour'");
await expect("tarefa repetida (mesma chave) recusada", "service_role", `insert into scheduled_jobs (kind, post_id, post_version, run_at, idempotency_key) values ('lembrete_publicacao', '${P}', 2, now(), 'k1')`, false);
await expect("anon nao pega tarefas", "anon", "select * from claim_jobs(10)", false);
await expect("authenticated nao pega tarefas", "authenticated", "select * from claim_jobs(10)", false);
const first = await as("service_role", "select idempotency_key, attempts, status from claim_jobs(10)");
report("pega so as vencidas, a mais antiga primeiro", first.map((j) => j.idempotency_key).join() === "k2,k1", JSON.stringify(first));
report("marca executando e conta tentativa", first.every((j) => j.status === "executando" && j.attempts === 1));
const second = await as("service_role", "select id from claim_jobs(10)");
report("a mesma tarefa nao e pega duas vezes", second.length === 0, `${second.length}`);
await db.query("update scheduled_jobs set locked_at = now() - interval '10 minutes' where idempotency_key='k1'");
const stale = await as("service_role", "select idempotency_key, attempts from claim_jobs(10)");
report("tarefa presa ha mais de 5 min volta", stale.length === 1 && stale[0].idempotency_key === "k1" && stale[0].attempts === 2, JSON.stringify(stale));
await expect("status do processador", "service_role", "insert into worker_status (id, last_result) values (1, '{}') on conflict (id) do update set last_run_at = now()", true);
await expect("so uma linha de status", "service_role", "insert into worker_status (id) values (2)", false);
await expect("tentativa registrada", "service_role", `insert into publishing_attempts (post_id, channel, outcome) values ('${P}', 'lembrete_telegram', 'ok')`, true);

const rls = (
  await db.query(
    "select count(*)::int as n from pg_class where relname in ('social_accounts','platform_connections','posts','post_media','post_destinations','approvals','scheduled_jobs','publishing_attempts','worker_status') and relrowsecurity"
  )
).rows[0].n;
report(`RLS ligado em ${rls}/9 tabelas`, rls === 9);
console.log(fails ? `${fails} FALHA(S)` : "TUDO OK");
process.exit(fails ? 1 : 0);

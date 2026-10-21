// Visao geral do modo manual. Por enquanto so apresenta o que vem nas
// proximas fases; os numeros entram quando houver biblioteca (Fase 2).
const NEXT_MODULES: { title: string; text: string }[] = [
  { title: "Conteúdos", text: "Biblioteca de imagens e vídeos enviados, guardados em armazenamento privado." },
  { title: "Calendário", text: "Agenda editorial com aprovação manual antes de publicar." },
  { title: "Publicações", text: "Instagram e TikTok pelas integrações oficiais." },
  { title: "Telegram", text: "Bot para conteúdo digital, assinaturas e vendas." },
  { title: "Relatórios", text: "Histórico de operações, audiência e financeiro." },
];

export function OverviewPage() {
  return (
    <div>
      <div className="page-header">
        <h1>Visão geral</h1>
        <p>Estúdio no modo manual: o conteúdo é gerado fora e enviado para cá. Nenhuma GPU é ligada por este app.</p>
      </div>

      <div className="overview-grid">
        {NEXT_MODULES.map((m) => (
          <div key={m.title} className="panel overview-card">
            <span className="overview-soon">Em breve</span>
            <h2>{m.title}</h2>
            <p>{m.text}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

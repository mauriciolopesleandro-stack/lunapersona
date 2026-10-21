// Visao geral do modo manual: o que ja funciona e o que vem nas proximas
// fases. Os numeros entram quando houver dados para resumir.
const MODULES: { title: string; text: string; ready?: boolean }[] = [
  { title: "Conteúdos", text: "Biblioteca de imagens e vídeos enviados, guardados em armazenamento privado.", ready: true },
  { title: "Calendário", text: "Agenda editorial com aprovação manual antes de publicar." },
  { title: "Publicações", text: "Instagram e TikTok pelas integrações oficiais." },
  { title: "Telegram", text: "Bot para conteúdo digital, assinaturas e vendas." },
  { title: "Relatórios", text: "Histórico de operações, audiência e financeiro." },
];

interface Props {
  onOpenLibrary: () => void;
}

export function OverviewPage({ onOpenLibrary }: Props) {
  return (
    <div>
      <div className="page-header">
        <h1>Visão geral</h1>
        <p>Estúdio no modo manual: o conteúdo é gerado fora e enviado para cá. Nenhuma GPU é ligada por este app.</p>
      </div>

      <div className="overview-grid">
        {MODULES.map((m) => (
          <div key={m.title} className="panel overview-card">
            <span className={m.ready ? "overview-soon overview-ready" : "overview-soon"}>
              {m.ready ? "Disponível" : "Em breve"}
            </span>
            <h2>{m.title}</h2>
            <p>{m.text}</p>
            {m.ready && (
              <button type="button" className="small overview-open" onClick={onOpenLibrary}>
                Abrir
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

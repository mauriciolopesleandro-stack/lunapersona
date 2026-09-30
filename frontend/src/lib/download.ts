// Baixa um arquivo do pod sem sair do site. O <a download> e ignorado pelo
// navegador quando o arquivo esta em outro endereco (o proxy da RunPod), e ai
// ele abre a imagem numa janela nova - no celular isso tirava a pessoa do site
// e a pagina recarregava sem o resultado. Baixando como blob, o endereco passa
// a ser do proprio site e o download funciona.
export async function downloadFile(url: string, filename: string): Promise<void> {
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blobUrl = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
  } catch {
    // Sem CORS/rede: ultimo recurso, abre numa aba nova (o site fica aberto nesta).
    window.open(url, "_blank", "noopener");
  }
}

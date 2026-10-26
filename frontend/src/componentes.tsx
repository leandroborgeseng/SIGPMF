export function Esqueleto({ linhas = 3 }: { linhas?: number }) {
  return (
    <div aria-busy="true" aria-live="polite" className="esqueleto">
      <span className="sr-only">Carregando…</span>
      {Array.from({ length: linhas }, (_, i) => <div key={i} className="esq-linha" />)}
    </div>
  )
}

export function Erro({ texto, onRetry }: { texto: string; onRetry: () => void }) {
  return (
    <div role="alert" className="aviso erro">
      <p>{texto}</p>
      <button className="btn" onClick={onRetry}>Tentar novamente</button>
    </div>
  )
}

const ROTULO: Record<string, string> = { sucesso: 'Sucesso', sem_novidades: 'Sem novidades', falha: 'Falha', em_andamento: 'Em andamento' }
export function SeloStatus({ status, novos, atualizados }: { status: string; novos: number; atualizados: number }) {
  if (status === 'sem_novidades')
    return <span className="selo neutro">Sem novidades · {novos} novos, {atualizados} atualizados</span>
  return <span className={`selo ${status}`}>{status === 'falha' ? '✕ ' : status === 'sucesso' ? '✓ ' : ''}{ROTULO[status] ?? status}</span>
}

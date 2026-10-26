import { Component, type ErrorInfo, type ReactNode } from 'react'

export function Esqueleto({ linhas = 3, texto = 'Carregando…' }: { linhas?: number; texto?: string }) {
  return (
    <div aria-busy="true" aria-live="polite" className="esqueleto">
      <span className="sr-only">{texto}</span>
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
    return <span className="selo neutro"><i aria-hidden="true" />Sem novidades · {novos} novos, {atualizados} atualizados</span>
  return <span className={`selo ${status}`}><i aria-hidden="true" />{status === 'falha' ? '✕ ' : status === 'sucesso' ? '✓ ' : ''}{ROTULO[status] ?? status}</span>
}

type PropsBoundary = { children: ReactNode; titulo?: string; onRetry?: () => void; onVoltar?: () => void; rotuloVoltar?: string; chave?: unknown }
type EstadoBoundary = { erro: Error | null; chave?: unknown }
/** Captura erro de render e mostra mensagem amigável em vez de tela branca. */
export class ErrorBoundary extends Component<PropsBoundary, EstadoBoundary> {
  state: EstadoBoundary = { erro: null, chave: this.props.chave }
  static getDerivedStateFromError(erro: Error) { return { erro } }
  static getDerivedStateFromProps(p: PropsBoundary, s: EstadoBoundary) {
    return p.chave !== s.chave ? { erro: null, chave: p.chave } : null
  }
  componentDidCatch(erro: Error, info: ErrorInfo) { console.error('Erro de tela', erro, info.componentStack) }
  render() {
    const { erro } = this.state
    if (!erro) return this.props.children
    const tentar = () => { this.setState({ erro: null }); this.props.onRetry?.() }
    return (
      <div role="alert" className="aviso erro">
        <p>{this.props.titulo ?? 'Não foi possível exibir esta tela.'}</p>
        <p className="nota">Código: ERRO_TELA · Motivo: {erro.name}: {erro.message}</p>
        <div className="acoes">
          <button className="btn" onClick={tentar}>Tentar de novo</button>
          {this.props.onVoltar && <button className="btn secundario" onClick={this.props.onVoltar}>{this.props.rotuloVoltar ?? 'Voltar'}</button>}
        </div>
      </div>
    )
  }
}

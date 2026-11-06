import { useState, type ReactNode } from 'react'

export type Fontes = { carregadas: Record<string, boolean>; nao_integradas: string[] }
export type Leitura = 'carregando' | 'ok' | 'sem_acesso' | 'falha'
export type EstadoArea = 'carregando' | 'tem' | 'nao_tem' | 'sem_base' | 'sem_acesso' | 'falha'

/** "Não tem" só com a base carregada e a leitura feita; ter registro vale mesmo com o aviso de base fora de ordem. */
export function estadoArea(leitura: Leitura, n: number, fontes: Fontes | null | false | undefined, chave: string): EstadoArea {
  if (leitura !== 'ok') return leitura
  if (n > 0) return 'tem'
  if (fontes === undefined) return 'nao_tem'  // bloco usado fora da ficha
  if (fontes === null) return 'carregando'
  if (fontes === false) return 'falha'
  return fontes.carregadas[chave] ? 'nao_tem' : 'sem_base'
}

const MSG: Partial<Record<EstadoArea, string>> = {
  carregando: 'Carregando…',
  sem_base: 'Esta base ainda não foi carregada no SIGPMF. Não dá para dizer se há registro.',
  sem_acesso: 'Seu perfil não tem acesso a esta área. Isso não quer dizer que não haja registro.',
  falha: 'Não foi possível ler esta área agora. Isso não quer dizer que não haja registro.',
}

/** Uma área da ficha: sempre aparece; com dados mostra os filhos, sem dados diz por quê. */
export function Area({ id, titulo, estado, vazio, total, children }: { id: string; titulo: string; estado: EstadoArea; vazio: string; total?: number; children?: ReactNode }) {
  return (
    <div className="area-ficha" id={`area-${id}`} tabIndex={-1}>
      <h4>{titulo}{estado === 'tem' && total != null ? <span className="sutil num"> · {total}</span> : null}</h4>
      {estado === 'tem' ? children
        : estado === 'nao_tem' ? <p className="area-vazia"><span aria-hidden="true">—</span> {vazio}</p>
        : <p className={estado === 'carregando' ? 'sutil' : 'area-indefinida'}>{MSG[estado]}</p>}
    </div>
  )
}

/** Mostra os primeiros itens; o resto, sob demanda. */
export function ListaCurta<T>({ itens, passo = 5, rotulo, render, classe = 'agenda-lista' }: { itens: T[]; passo?: number; rotulo: string; render: (x: T, i: number) => ReactNode; classe?: string }) {
  const [todos, setTodos] = useState(false)
  return (
    <>
      <ul className={classe} aria-label={rotulo}>{(todos ? itens : itens.slice(0, passo)).map(render)}</ul>
      {itens.length > passo && <button className="btn-link" aria-expanded={todos} onClick={() => setTodos(!todos)}>{todos ? 'Mostrar menos' : `Ver todos (${itens.length})`}</button>}
    </>
  )
}

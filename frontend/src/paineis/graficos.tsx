import { useCallback, useEffect, useState } from 'react'
import { api, dataHoraBR } from '../api'

/** Ponto de uma série do ouro agregado (GET /api/indicadores). */
export type Ponto = { periodo: string; dim: string; valor: number }
export type RespIndicadores = { atualizado_em: string | null; series: Record<string, Ponto[]> }

const valida = (r: unknown): r is RespIndicadores => !!r && typeof (r as RespIndicadores).series === 'object' && (r as RespIndicadores).series !== null

/** Lê as séries do painel; nunca lança (erro vira `falhou`). */
export function useIndicadores(series: string[]) {
  const chave = series.join(',')
  const [d, setD] = useState<RespIndicadores | null>(null)
  const [falhou, setFalhou] = useState(false)
  const carregar = useCallback(() => {
    setD(null); setFalhou(false)
    api<unknown>(`/indicadores?series=${encodeURIComponent(chave)}`).then((r) => (valida(r) ? setD(r) : setFalhou(true))).catch(() => setFalhou(true))
  }, [chave])
  useEffect(carregar, [carregar])
  return { d, falhou, carregar }
}

export const kpi = (d: RespIndicadores | null, dim: string): number | null => {
  const p = d?.series.kpi?.find((x) => x.dim === dim)
  return p ? p.valor : null
}
export const serie = (d: RespIndicadores | null, nome: string): Ponto[] => d?.series[nome] ?? []

const nf = new Intl.NumberFormat('pt-BR')
export const num = (v: number | null) => (v == null ? '—' : nf.format(Math.round(v)))
export const reais = (v: number | null) => {
  if (v == null) return '—'
  const a = Math.abs(v)
  if (a >= 1e9) return `R$ ${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 }).format(v / 1e9)} bi`
  if (a >= 1e6) return `R$ ${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(v / 1e6)} mi`
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(v)
}
export const pct = (v: number | null) => (v == null ? '—' : `${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(v)}%`)
const MES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
export const mesCurto = (p: string) => { const [a, m] = p.split('-'); return m ? `${MES[Number(m) - 1]}/${a.slice(2)}` : p }

export function Cabecalho({ kicker, titulo, sub, atualizado }: { kicker: string; titulo: string; sub: string; atualizado?: string | null }) {
  return (
    <header className="page-head painel-cab">
      <div>
        <p className="kicker">{kicker}</p>
        <h1>{titulo}</h1>
        <p className="sutil">{sub}</p>
      </div>
      {atualizado && <span className="selo neutro"><i aria-hidden="true" />Dados de {dataHoraBR(atualizado)}</span>}
    </header>
  )
}

export function Kpi({ rotulo, valor, nota, tom = 'normal' }: { rotulo: string; valor: string; nota?: string; tom?: 'normal' | 'alerta' | 'bom' }) {
  return (
    <div className={`cartao kpi kpi-${tom}`}>
      <span className="cartao-rotulo">{rotulo}</span>
      <span className="cartao-valor num">{valor}</span>
      {nota && <span className="kpi-nota">{nota}</span>}
    </div>
  )
}

export function Painel({ titulo, nota, children, largo }: { titulo: string; nota?: string; children: React.ReactNode; largo?: boolean }) {
  return (
    <section className={`cartao painel-bloco${largo ? ' largo' : ''}`} aria-label={titulo}>
      <div className="painel-bloco-cab"><h2>{titulo}</h2>{nota && <span className="sutil">{nota}</span>}</div>
      {children}
    </section>
  )
}

export function Vazio({ texto = 'Sem dados ainda: rode as cargas deste conector.' }: { texto?: string }) {
  return <p className="sutil painel-vazio">{texto}</p>
}

/** Barras horizontais ordenadas (maior primeiro), com o valor formatado à direita. */
export function BarrasH({ pontos, formato = num, max = 10, cor = 'brand', rotulo }: {
  pontos: Ponto[]; formato?: (v: number) => string; max?: number; cor?: 'brand' | 'alerta' | 'ok'; rotulo: string
}) {
  const lista = [...pontos].sort((a, b) => b.valor - a.valor).slice(0, max)
  if (lista.length === 0) return <Vazio />
  const topo = Math.max(...lista.map((p) => p.valor), 1)
  return (
    <ul className="barras-h" aria-label={rotulo}>
      {lista.map((p) => (
        <li key={p.dim}>
          <span className="barras-h-rotulo" title={p.dim}>{p.dim}</span>
          <span className="barras-h-trilho" aria-hidden="true"><span className={`barras-h-barra ${cor}`} style={{ width: `${Math.max(2, (p.valor / topo) * 100)}%` }} /></span>
          <span className="barras-h-valor num">{formato(p.valor)}</span>
        </li>
      ))}
    </ul>
  )
}

/** Colunas por mês (uma ou duas séries), últimos `meses`. Lista acessível junto. */
export function ColunasMes({ a, b, rotuloA, rotuloB, formato = num, meses = 12 }: {
  a: Ponto[]; b?: Ponto[]; rotuloA: string; rotuloB?: string; formato?: (v: number) => string; meses?: number
}) {
  const periodos = [...new Set([...a, ...(b ?? [])].map((p) => p.periodo).filter(Boolean))].sort().slice(-meses)
  if (periodos.length === 0) return <Vazio />
  const va = new Map(a.map((p) => [p.periodo, p.valor]))
  const vb = new Map((b ?? []).map((p) => [p.periodo, p.valor]))
  const topo = Math.max(...periodos.flatMap((p) => [va.get(p) ?? 0, vb.get(p) ?? 0]), 1)
  return (
    <div className="colunas">
      <div className="colunas-legenda">
        <span><i className="leg a" aria-hidden="true" />{rotuloA}</span>
        {b && rotuloB && <span><i className="leg b" aria-hidden="true" />{rotuloB}</span>}
      </div>
      <div className="colunas-grade" aria-hidden="true" style={{ gridTemplateColumns: `repeat(${periodos.length}, minmax(0, 1fr))` }}>
        {periodos.map((p) => (
          <div key={p} className="colunas-mes">
            <div className="colunas-barras">
              <span className="col a" style={{ height: `${((va.get(p) ?? 0) / topo) * 100}%` }} />
              {b && <span className="col b" style={{ height: `${((vb.get(p) ?? 0) / topo) * 100}%` }} />}
            </div>
            <span className="colunas-rotulo">{mesCurto(p)}</span>
          </div>
        ))}
      </div>
      <table className="sr-only">
        <caption>{rotuloA}{rotuloB ? ` e ${rotuloB}` : ''} por mês</caption>
        <tbody>{periodos.map((p) => <tr key={p}><th scope="row">{mesCurto(p)}</th><td>{formato(va.get(p) ?? 0)}</td>{b && <td>{formato(vb.get(p) ?? 0)}</td>}</tr>)}</tbody>
      </table>
    </div>
  )
}

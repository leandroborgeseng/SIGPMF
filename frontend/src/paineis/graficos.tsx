import { useCallback, useEffect, useState, type KeyboardEvent } from 'react'
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
export const mesCurto = (p: string) => { const [a, m] = p.split('-'); return !m ? p : m === '13' ? `13º/${a.slice(2)}` : `${MES[Number(m) - 1]}/${a.slice(2)}` }
/** Número curto para eixo: 1,2 mil · 3 mi · 1,5 bi. */
export const compacto = (v: number) => {
  const f = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 })
  const a = Math.abs(v)
  return a >= 1e9 ? `${f.format(v / 1e9)} bi` : a >= 1e6 ? `${f.format(v / 1e6)} mi` : a >= 1e3 ? `${f.format(v / 1e3)} mil` : f.format(v)
}

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

/** Linha fina do histórico dentro do KPI; o valor exato segue no próprio KPI e nos gráficos da página. */
function Mini({ valores }: { valores: number[] }) {
  if (valores.length < 2) return null
  // Variação pequena fica quase reta: a escala nunca é menor que 10% do valor, para não exagerar oscilação à toa
  const topo = Math.max(...valores), base = Math.min(...valores), faixa = Math.max(topo - base, Math.abs(topo) * 0.1) || 1
  const pts = valores.map((v, i) => `${(i / (valores.length - 1)) * 100},${28 - ((v - base) / faixa) * 26}`).join(' ')
  return <svg className="kpi-mini" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><polyline points={pts} vectorEffect="non-scaling-stroke" /></svg>
}

export function Kpi({ rotulo, valor, nota, tom = 'normal', mini }: { rotulo: string; valor: string; nota?: string; tom?: 'normal' | 'alerta' | 'bom'; mini?: number[] }) {
  return (
    <div className={`cartao kpi kpi-${tom}`}>
      <span className="cartao-rotulo">{rotulo}</span>
      <span className="cartao-valor">{valor}</span>
      {nota && <span className="kpi-nota">{nota}</span>}
      {mini && <Mini valores={mini} />}
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

/** Barras horizontais ordenadas (maior primeiro), com o valor formatado à direita. `aoEscolher` filtra a página (a
 * escolhida fica marcada por `ativo`); `aoAbrir` abre o detalhe daquela barra. */
export function BarrasH({ pontos, formato = num, max = 10, rotulo, aoEscolher, ativo, aoAbrir, naOrdem }: {
  pontos: Ponto[]; formato?: (v: number) => string; max?: number; rotulo: string
  aoEscolher?: (dim: string) => void; ativo?: string | null; aoAbrir?: (dim: string) => void; naOrdem?: boolean
}) {
  const lista = (naOrdem ? pontos : [...pontos].sort((a, b) => b.valor - a.valor)).slice(0, max)
  if (lista.length === 0) return <Vazio />
  const topo = Math.max(...lista.map((p) => p.valor), 1)
  const conteudo = (p: Ponto) => (
    <>
      <span className="barras-h-rotulo" title={p.dim}>{p.dim}</span>
      <span className="barras-h-trilho" aria-hidden="true"><span className="barras-h-barra" style={{ width: `${Math.max(1, (p.valor / topo) * 100)}%` }} /></span>
      <span className="barras-h-valor num">{formato(p.valor)}</span>
    </>
  )
  return (
    <ul className={`barras-h${aoEscolher || aoAbrir ? ' clicavel' : ''}`} aria-label={rotulo}>
      {lista.map((p) => (
        <li key={p.dim} className={ativo === p.dim ? 'ativo' : undefined}>
          {aoEscolher ? <button type="button" aria-pressed={ativo === p.dim} onClick={() => aoEscolher(p.dim)}>{conteudo(p)}</button>
            : aoAbrir ? <button type="button" title="Abrir detalhe" onClick={() => aoAbrir(p.dim)}>{conteudo(p)}</button>
            : conteudo(p)}
        </li>
      ))}
    </ul>
  )
}

function escala(maximo: number) {
  const bruto = Math.max(maximo, 1) / 4
  const p = 10 ** Math.floor(Math.log10(bruto))
  const n = bruto / p
  const passo = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p
  const topo = Math.ceil(Math.max(maximo, 1) / passo) * passo
  const marcas: number[] = []
  for (let v = 0; v <= topo + passo / 2; v += passo) marcas.push(v)
  return { topo, marcas }
}

/** Evolução no tempo, em uma ou duas séries: colunas até 24 períodos, linha acima disso. Com eixo, dica ao passar o mouse
 * ou com as setas do teclado, e tabela para leitor de tela. Com `aoAbrir`, cada período abre o detalhe. */
export function ColunasMes({ a, b, rotuloA, rotuloB, formato = num, meses = 12, aoAbrir }: {
  a: Ponto[]; b?: Ponto[]; rotuloA: string; rotuloB?: string; formato?: (v: number) => string; meses?: number; aoAbrir?: (periodo: string) => void
}) {
  const [foco, setFoco] = useState<number | null>(null)
  const periodos = [...new Set([...a, ...(b ?? [])].map((p) => p.periodo).filter(Boolean))].sort().slice(-meses)
  if (periodos.length === 0) return <Vazio />
  const va = new Map(a.map((p) => [p.periodo, p.valor]))
  const vb = new Map((b ?? []).map((p) => [p.periodo, p.valor]))
  const { topo, marcas } = escala(Math.max(...periodos.flatMap((p) => [va.get(p) ?? 0, vb.get(p) ?? 0])))
  const n = periodos.length
  const linha = n > 24
  const passoX = Math.ceil(n / 12)  // no máximo 12 rótulos no eixo; o último período sempre aparece
  const x = (i: number) => ((i + 0.5) / n) * 100
  const y = (v: number) => 100 - (Math.max(v, 0) / topo) * 100
  const pontos = (m: Map<string, number>) => periodos.map((p, i) => `${x(i)},${y(m.get(p) ?? 0)}`).join(' ')
  const teclas = (e: KeyboardEvent) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      setFoco((f) => Math.min(n - 1, Math.max(0, (f ?? (e.key === 'ArrowRight' ? -1 : n)) + (e.key === 'ArrowRight' ? 1 : -1))))
    } else if (e.key === 'Enter' && aoAbrir && foco != null) aoAbrir(periodos[foco])
    else if (e.key === 'Escape') setFoco(null)
  }
  const rotuloMes = `${rotuloA}${rotuloB ? ` e ${rotuloB}` : ''} por mês`
  return (
    <div className="colunas">
      {b && rotuloB && (
        <div className="colunas-legenda">
          <span><i className="leg a" aria-hidden="true" />{rotuloA}</span>
          <span><i className="leg b" aria-hidden="true" />{rotuloB}</span>
        </div>
      )}
      <div className="grafico">
        <div className="grafico-y" aria-hidden="true">
          {marcas.map((m) => <span key={m} style={{ bottom: `${(m / topo) * 100}%` }}>{compacto(m)}</span>)}
        </div>
        <div className={`grafico-plot${aoAbrir ? ' clicavel' : ''}`} role="img" tabIndex={0} aria-label={`${rotuloMes}. Use as setas para ler cada período${aoAbrir ? ' e Enter para abrir o detalhe' : ''}.`}
          onKeyDown={teclas} onMouseLeave={() => setFoco(null)} onBlur={() => setFoco(null)}>
          {marcas.map((m) => <i key={m} className="grafico-grade" style={{ bottom: `${(m / topo) * 100}%` }} />)}
          {linha && (
            <svg className="grafico-linha" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              {!b && <polygon className="area" points={`${x(0)},100 ${pontos(va)} ${x(n - 1)},100`} />}
              <polyline className="a" points={pontos(va)} vectorEffect="non-scaling-stroke" />
              {b && <polyline className="b" points={pontos(vb)} vectorEffect="non-scaling-stroke" />}
            </svg>
          )}
          <div className="grafico-colunas" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
            {periodos.map((p, i) => (
              <div key={p} className={`grafico-coluna${foco === i ? ' foco' : ''}`} onMouseEnter={() => setFoco(i)} onClick={aoAbrir ? () => aoAbrir(p) : undefined}>
                {!linha && (
                  <>
                    <span className="col a" style={{ height: `${((va.get(p) ?? 0) / topo) * 100}%` }} />
                    {b && <span className="col b" style={{ height: `${((vb.get(p) ?? 0) / topo) * 100}%` }} />}
                  </>
                )}
              </div>
            ))}
          </div>
          {foco != null && (
            <>
              {linha && <i className="grafico-mira" style={{ left: `${x(foco)}%` }} />}
              <div className="grafico-dica" style={{ left: `${x(foco)}%`, transform: `translateX(${x(foco) > 70 ? '-100%' : x(foco) < 30 ? '0' : '-50%'})` }}>
                <span className="grafico-dica-per">{mesCurto(periodos[foco])}</span>
                <span><i className="chave a" /><strong>{formato(va.get(periodos[foco]) ?? 0)}</strong> {rotuloA}</span>
                {b && <span><i className="chave b" /><strong>{formato(vb.get(periodos[foco]) ?? 0)}</strong> {rotuloB}</span>}
                {aoAbrir && <span className="grafico-dica-acao">clique para detalhar</span>}
              </div>
            </>
          )}
        </div>
        <div className="grafico-x" aria-hidden="true" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
          {periodos.map((p, i) => <span key={p}>{(n - 1 - i) % passoX === 0 ? mesCurto(p) : ''}</span>)}
        </div>
      </div>
      <table className="sr-only">
        <caption>{rotuloMes}</caption>
        <tbody>{periodos.map((p) => <tr key={p}><th scope="row">{mesCurto(p)}</th><td>{formato(va.get(p) ?? 0)}</td>{b && <td>{formato(vb.get(p) ?? 0)}</td>}</tr>)}</tbody>
      </table>
    </div>
  )
}

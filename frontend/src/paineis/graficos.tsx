import { createContext, useCallback, useContext, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { api, dataHoraBR } from '../api'
import { nomeFonte } from '../OrigemDado'

/** Ponto de uma série do ouro agregado (GET /api/indicadores). */
export type Ponto = { periodo: string; dim: string; valor: number }
export type Procedencia = { sistema: string; fonte_atualizado_em?: string | null }
export type RespIndicadores = { atualizado_em: string | null; series: Record<string, Ponto[]>; procedencia?: Record<string, Procedencia> }

/** Tema único dos gráficos (contraste AA do traço sobre o fundo claro). */
export const TEMA = {
  a: '#0B5CAB',
  b: '#C2410C',
  tendencia: '#1C1917',
  grade: '#E2E8F0',
  rotulo: '#1A2332',
} as const

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

/** Rótulo visível: abrevia mil/mi/bi e preserva R$, % e unidade que o formato já trouxe. */
export function rotuloDado(v: number, formato: (n: number) => string = num): string {
  const cheio = formato(v)
  if (/%|R\$| bi$| mi$| mil$/.test(cheio) || /\sd$/.test(cheio)) return cheio
  return Math.abs(v) >= 1000 ? compacto(v) : cheio
}

/** Regressão linear (y = a·x + b) na ordem dos pontos. Série curta devolve a própria série. */
export function regressaoLinear(valores: number[]): number[] {
  const n = valores.length
  if (n === 0) return []
  if (n === 1) return [valores[0]]
  let sx = 0, sy = 0, sxx = 0, sxy = 0
  valores.forEach((y, x) => { sx += x; sy += y; sxx += x * x; sxy += x * y })
  const den = n * sxx - sx * sx || 1
  const a = (n * sxy - sx * sy) / den
  const b = (sy - a * sx) / n
  return valores.map((_, i) => a * i + b)
}

/**
 * Índices cujos rótulos cabem lado a lado. Quando não cabe, esconde o que sobrepõe
 * (o último ponto entra se houver folga no fim).
 */
export function indicesSemSobreposicao(n: number, largura = 720, larguraRotulo = 46): number[] {
  if (n <= 0) return []
  const passo = largura / n
  const vis: number[] = []
  let ocupadoAte = -Infinity
  for (let i = 0; i < n; i++) {
    const centro = (i + 0.5) * passo
    const ini = centro - larguraRotulo / 2
    const fim = centro + larguraRotulo / 2
    if (ini >= ocupadoAte - 0.01) {
      vis.push(i)
      ocupadoAte = fim
    }
  }
  return vis
}

/** Folga acima da área do gráfico, em px, para o rótulo da barra mais alta. */
export const FOLGA_ROTULO = 22

export type CaixaRotulo = {
  x: number; y: number; w: number; h: number
  texto: string; serie: 'a' | 'b'; barra: number; px: number
}

/** Verdadeiro quando os retângulos se cruzam (borda compartilhada não conta). */
export function caixasCruzam(a: Pick<CaixaRotulo, 'x' | 'y' | 'w' | 'h'>, b: Pick<CaixaRotulo, 'x' | 'y' | 'w' | 'h'>, folga = 0): boolean {
  return a.x < b.x + b.w - folga && a.x + a.w > b.x + folga && a.y < b.y + b.h - folga && a.y + a.h > b.y + folga
}

/** Folga extra entre rótulos, para o halo de um não encostar no outro. */
function rotulosCruzam(a: Pick<CaixaRotulo, 'x' | 'y' | 'w' | 'h'>, b: Pick<CaixaRotulo, 'x' | 'y' | 'w' | 'h'>): boolean {
  const m = 3
  return caixasCruzam({ x: a.x - m, y: a.y - m, w: a.w + m * 2, h: a.h + m * 2 }, b)
}

function medidaRotulo(texto: string, px: number) {
  return { w: Math.ceil(texto.length * px * 0.7) + 10, h: px + 8 }
}

function centroBarra(i: number, n: number, largura: number, serie: 'a' | 'b', duas: boolean) {
  const colW = largura / n
  const gap = duas ? 2 : 0
  const inner = colW * 0.76
  const barW = Math.min(24, Math.max(4, (inner - gap) / (duas ? 2 : 1)))
  const grupo = duas ? barW * 2 + gap : barW
  const start = i * colW + (colW - grupo) / 2
  const cx = !duas || serie === 'a' ? start + barW / 2 : start + barW + gap + barW / 2
  return { cx, barW }
}

/**
 * Rótulos das barras agrupadas. Cada um fica acima da própria barra.
 * Se cruzar a outra barra, sobe até livrá-la. Se ainda cruzar outro rótulo,
 * tenta uma fonte menor e, se não couber, fica só uma série naquele mês (alternando).
 */
export function colocaRotulos(opts: {
  valoresA: number[]
  valoresB?: number[]
  topo: number
  largura: number
  altura: number
  texto: (v: number, serie: 'a' | 'b') => string
}): CaixaRotulo[] {
  const { valoresA, valoresB, topo, largura, altura, texto } = opts
  const n = valoresA.length
  if (n === 0 || topo <= 0) return []
  const duas = !!valoresB
  const yBarra = (v: number) => altura * (1 - Math.max(v, 0) / topo)
  const obstaculos: Array<Pick<CaixaRotulo, 'x' | 'y' | 'w' | 'h'> & { i: number; serie: 'a' | 'b' }> = []
  for (let i = 0; i < n; i++) {
    for (const serie of (duas ? ['a', 'b'] : ['a']) as Array<'a' | 'b'>) {
      const v = serie === 'a' ? valoresA[i] : valoresB![i]
      const { cx, barW } = centroBarra(i, n, largura, serie, duas)
      const y = yBarra(v)
      obstaculos.push({ i, serie, x: cx - barW / 2, y, w: barW, h: Math.max(0, altura - y) })
    }
  }
  const aceitos: CaixaRotulo[] = []
  for (let i = 0; i < n; i++) {
    const ordem: Array<'a' | 'b'> = !duas ? ['a'] : i % 2 === 0 ? ['a', 'b'] : ['b', 'a']
    for (const serie of ordem) {
      const v = serie === 'a' ? valoresA[i] : valoresB![i]
      const t = texto(v, serie)
      const { cx } = centroBarra(i, n, largura, serie, duas)
      const barra = yBarra(v)
      let escolhida: CaixaRotulo | null = null
      for (const px of [10, 8]) {
        const { w, h } = medidaRotulo(t, px)
        let caixa: CaixaRotulo = { x: cx - w / 2, y: barra - 3 - h, w, h, texto: t, serie, barra, px }
        for (let k = 0; k < 4; k++) {
          const cruzam = obstaculos.filter((o) => (o.i !== i || o.serie !== serie) && caixasCruzam(caixa, o))
          if (cruzam.length === 0) break
          caixa = { ...caixa, y: Math.min(...cruzam.map((o) => o.y)) - 3 - h }
        }
        const acima = caixa.y + caixa.h <= barra + 0.01
        const naFolga = caixa.y >= -FOLGA_ROTULO
        const livre = aceitos.every((o) => !rotulosCruzam(o, caixa))
        if (acima && naFolga && livre) { escolhida = caixa; break }
      }
      if (escolhida) aceitos.push(escolhida)
    }
  }
  return aceitos
}

export function dataFonte(iso?: string | null): string | null {
  const m = iso ? /^(\d{4})-(\d{2})-(\d{2})/.exec(iso) : null
  return m ? `${m[3]}/${m[2]}/${m[1]}` : null
}

/** Rodapé "Fonte: … · atualizado em dd/mm/aaaa". */
export function FonteRodape({ fonte, atualizadoEm }: { fonte?: string | null; atualizadoEm?: string | null }) {
  if (!fonte) return null
  const quando = dataFonte(atualizadoEm)
  const nomes = fonte.split(' · ').map((f) => nomeFonte(f.trim())).join(' · ')
  const texto = quando ? `Fonte: ${nomes} · atualizado em ${quando}` : `Fonte: ${nomes}`
  return <p className="fonte-rodape" role="note">{texto}</p>
}

const FonteCtx = createContext<{ fonte?: string; atualizadoEm?: string | null }>({})

/** Uma procedência para todos os painéis filhos (gráficos e tabelas do bloco). */
export function FontesPainel({ fonte, atualizadoEm, children }: { fonte: string; atualizadoEm?: string | null; children: ReactNode }) {
  return <FonteCtx.Provider value={{ fonte, atualizadoEm }}>{children}</FonteCtx.Provider>
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

function unidadeDe(formato: (n: number) => string, unidade?: string): string | null {
  if (unidade) return unidade
  const amostra = formato(1000)
  if (amostra.includes('R$')) return 'R$'
  if (amostra.includes('%')) return '%'
  if (/\sd$/.test(amostra)) return 'dias'
  return null
}

/** Linha fina do histórico dentro do KPI, com o primeiro e o último valor e a tendência. */
function Mini({ valores }: { valores: number[] }) {
  if (valores.length < 2) return null
  const topo = Math.max(...valores), base = Math.min(...valores), faixa = Math.max(topo - base, Math.abs(topo) * 0.1) || 1
  const y = (v: number) => 22 - ((v - base) / faixa) * 16
  const pts = valores.map((v, i) => `${(i / (valores.length - 1)) * 100},${y(v)}`).join(' ')
  const tend = regressaoLinear(valores).map((v, i) => `${(i / (valores.length - 1)) * 100},${y(v)}`).join(' ')
  return (
    <span className="kpi-mini-bloco">
      <svg className="kpi-mini" viewBox="0 0 100 30" preserveAspectRatio="none" role="img"
        aria-label={`Série de ${rotuloDado(valores[0])} a ${rotuloDado(valores[valores.length - 1])}, com linha de tendência`}>
        <polyline className="tendencia" points={tend} vectorEffect="non-scaling-stroke" />
        <polyline points={pts} vectorEffect="non-scaling-stroke" />
      </svg>
      <span className="kpi-mini-rotulos"><span>{rotuloDado(valores[0])}</span><span>{rotuloDado(valores[valores.length - 1])}</span></span>
      <span className="kpi-mini-legenda"><i className="leg a" aria-hidden="true" />Série <i className="leg tendencia" aria-hidden="true" />Tendência</span>
    </span>
  )
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

export function Painel({ titulo, nota, children, largo, fonte, atualizadoEm }: {
  titulo: string; nota?: string; children: React.ReactNode; largo?: boolean; fonte?: string | null; atualizadoEm?: string | null
}) {
  const ctx = useContext(FonteCtx)
  const f = fonte ?? ctx.fonte
  const em = atualizadoEm ?? ctx.atualizadoEm
  return (
    <section className={`cartao painel-bloco${largo ? ' largo' : ''}`} aria-label={titulo}>
      <div className="painel-bloco-cab"><h2>{titulo}</h2>{nota && <span className="sutil">{nota}</span>}</div>
      {children}
      <FonteRodape fonte={f} atualizadoEm={em} />
    </section>
  )
}

export function Vazio({ texto = 'Sem dados ainda: rode as cargas deste conector.' }: { texto?: string }) {
  return <p className="sutil painel-vazio">{texto}</p>
}

/** Barras horizontais ordenadas (maior primeiro), com o valor formatado à direita. `aoEscolher` filtra a página (a
 * escolhida fica marcada por `ativo`); `aoAbrir` abre o detalhe daquela barra. */
export function BarrasH({ pontos, formato = num, max = 10, rotulo, aoEscolher, ativo, aoAbrir, naOrdem, unidade, fonte, atualizadoEm }: {
  pontos: Ponto[]; formato?: (v: number) => string; max?: number; rotulo: string
  aoEscolher?: (dim: string) => void; ativo?: string | null; aoAbrir?: (dim: string) => void; naOrdem?: boolean
  unidade?: string; fonte?: string | null; atualizadoEm?: string | null
}) {
  const lista = (naOrdem ? pontos : [...pontos].sort((a, b) => b.valor - a.valor)).slice(0, max)
  if (lista.length === 0) return <Vazio />
  const topo = Math.max(...lista.map((p) => p.valor), 1)
  const un = unidadeDe(formato, unidade)
  const conteudo = (p: Ponto) => (
    <>
      <span className="barras-h-rotulo" title={p.dim}>{p.dim}</span>
      <span className="barras-h-trilho" aria-hidden="true"><span className="barras-h-barra" style={{ width: `${Math.max(1, (p.valor / topo) * 100)}%` }} /></span>
      <span className="barras-h-valor num" title={formato(p.valor)}>{rotuloDado(p.valor, formato)}</span>
    </>
  )
  return (
    <div className="barras-bloco">
      <div className="colunas-legenda">
        <span><i className="leg a" aria-hidden="true" />{rotulo}</span>
        {un && <span>Unidade: {un}</span>}
      </div>
      <ul className={`barras-h${aoEscolher || aoAbrir ? ' clicavel' : ''}`} aria-label={rotulo}>
        {lista.map((p) => (
          <li key={p.dim} className={ativo === p.dim ? 'ativo' : undefined}>
            {aoEscolher ? <button type="button" aria-pressed={ativo === p.dim} onClick={() => aoEscolher(p.dim)}>{conteudo(p)}</button>
              : aoAbrir ? <button type="button" title="Abrir detalhe" onClick={() => aoAbrir(p.dim)}>{conteudo(p)}</button>
              : conteudo(p)}
          </li>
        ))}
      </ul>
      <div className="barras-eixo" aria-hidden="true">
        <span />
        <span className="barras-eixo-marcas"><i>0</i><i>{compacto(topo / 2)}</i><i>{compacto(topo)}</i></span>
        <span />
      </div>
      <FonteRodape fonte={fonte} atualizadoEm={atualizadoEm} />
    </div>
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

/** Evolução no tempo, em uma ou duas séries: colunas até 24 períodos, linha acima disso. Rótulos visíveis (mil/mi/bi)
 * só onde cabem, linha de tendência na legenda, eixo, unidade e dica ao passar o mouse ou com as setas. */
function useTamanho() {
  const ref = useRef<HTMLDivElement>(null)
  const [tam, setTam] = useState({ w: 0, h: 0 })
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const mede = () => setTam({ w: el.clientWidth, h: el.clientHeight })
    mede()
    const o = new ResizeObserver(mede)
    o.observe(el)
    return () => o.disconnect()
  }, [])
  return { ref, ...tam }
}

export function ColunasMes({ a, b, rotuloA, rotuloB, formato = num, meses = 12, aoAbrir, titulo, unidade, fonte, atualizadoEm, temporal = true }: {
  a: Ponto[]; b?: Ponto[]; rotuloA: string; rotuloB?: string; formato?: (v: number) => string; meses?: number; aoAbrir?: (periodo: string) => void
  titulo?: string; unidade?: string; fonte?: string | null; atualizadoEm?: string | null; temporal?: boolean
}) {
  const [foco, setFoco] = useState<number | null>(null)
  const { ref: areaRef, w: largura, h: altura } = useTamanho()
  const larguraUtil = largura > 40 ? largura : 720
  const alturaUtil = altura > 40 ? altura : 214
  const periodos = [...new Set([...a, ...(b ?? [])].map((p) => p.periodo).filter(Boolean))].sort().slice(-meses)
  if (periodos.length === 0) return <Vazio />
  const va = new Map(a.map((p) => [p.periodo, p.valor]))
  const vb = new Map((b ?? []).map((p) => [p.periodo, p.valor]))
  const valsA = periodos.map((p) => va.get(p) ?? 0)
  const { topo, marcas } = escala(Math.max(...periodos.flatMap((p) => [va.get(p) ?? 0, vb.get(p) ?? 0]), ...(temporal ? regressaoLinear(valsA) : [])))
  const n = periodos.length
  const linha = n > 24
  const passoX = Math.max(Math.ceil(n / 12), Math.ceil((n * 42) / larguraUtil))
  const x = (i: number) => ((i + 0.5) / n) * 100
  const y = (v: number) => 100 - (Math.max(v, 0) / topo) * 100
  const pontos = (m: Map<string, number>) => periodos.map((p, i) => `${x(i)},${y(m.get(p) ?? 0)}`).join(' ')
  const tend = temporal ? regressaoLinear(valsA) : []
  const un = unidadeDe(formato, unidade)
  const teclas = (e: KeyboardEvent) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      setFoco((f) => Math.min(n - 1, Math.max(0, (f ?? (e.key === 'ArrowRight' ? -1 : n)) + (e.key === 'ArrowRight' ? 1 : -1))))
    } else if (e.key === 'Enter' && aoAbrir && foco != null) aoAbrir(periodos[foco])
    else if (e.key === 'Escape') setFoco(null)
  }
  const rotuloMes = `${rotuloA}${rotuloB ? ` e ${rotuloB}` : ''} por mês`
  const textoDe = (v: number) => rotuloDado(v, formato)
  const yPonto = (v: number) => alturaUtil * (1 - Math.max(v, 0) / topo)
  const caixas: CaixaRotulo[] = linha
    ? indicesSemSobreposicao(n, larguraUtil, largura > 40 ? 54 : 46).flatMap((i, k) => {
      const ordem: Array<'a' | 'b'> = !b ? ['a'] : k % 2 === 0 ? ['a', 'b'] : ['b', 'a']
      const postas: CaixaRotulo[] = []
      for (const serie of ordem) {
        const v = serie === 'a' ? valsA[i] : (vb.get(periodos[i]) ?? 0)
        const t = textoDe(v)
        const { w: rw, h: rh } = medidaRotulo(t, 10)
        const cx = ((i + 0.5) / n) * larguraUtil
        const barra = yPonto(v)
        const caixa: CaixaRotulo = { x: cx - rw / 2, y: barra - 3 - rh, w: rw, h: rh, texto: t, serie, barra, px: 10 }
        const livres = [...postas, ...([] as CaixaRotulo[])]
        if (livres.every((o) => !rotulosCruzam(o, caixa)) && caixa.y >= -FOLGA_ROTULO) postas.push(caixa)
      }
      return postas
    })
    : colocaRotulos({ valoresA: valsA, valoresB: b ? periodos.map((p) => vb.get(p) ?? 0) : undefined, topo, largura: larguraUtil, altura: alturaUtil, texto: (v) => textoDe(v) })
  return (
    <div className="colunas">
      <p className="grafico-meta"><strong>{titulo ?? rotuloMes}</strong>{un && <span>Unidade: {un}</span>}</p>
      <div className="colunas-legenda">
        <span><i className="leg a" aria-hidden="true" />{rotuloA}</span>
        {b && rotuloB && <span><i className="leg b" aria-hidden="true" />{rotuloB}</span>}
        {temporal && <span><i className="leg tendencia" aria-hidden="true" />Tendência</span>}
      </div>
      <div className="grafico">
        <div className="grafico-y" aria-hidden="true">
          <div className="grafico-folga" />
          <div className="grafico-y-area">
            {marcas.map((m) => <span key={m} style={{ bottom: `${(m / topo) * 100}%` }}>{compacto(m)}</span>)}
          </div>
        </div>
        <div className={`grafico-plot${aoAbrir ? ' clicavel' : ''}`} role="img" tabIndex={0} aria-label={`${rotuloMes}. Use as setas para ler cada período${aoAbrir ? ' e Enter para abrir o detalhe' : ''}.`}
          onKeyDown={teclas} onMouseLeave={() => setFoco(null)} onBlur={() => setFoco(null)}>
          <div className="grafico-folga" />
          <div className="grafico-area" ref={areaRef}>
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
            {temporal && (
              <svg className="grafico-tendencia" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                <polyline points={tend.map((v, i) => `${x(i)},${y(v)}`).join(' ')} vectorEffect="non-scaling-stroke" />
              </svg>
            )}
            <div className="grafico-rotulos">
              {caixas.map((c) => (
                <span key={`${c.serie}-${c.x}`} className="grafico-rotulo-ancora" style={{ left: `${((c.x + c.w / 2) / larguraUtil) * 100}%`, bottom: `${((alturaUtil - c.y - c.h) / alturaUtil) * 100}%` }}>
                  <svg width={c.w} height={c.h} viewBox={`0 0 ${c.w} ${c.h}`} aria-hidden="true">
                    <rect width={c.w} height={c.h} rx="3" fill="#ffffff" />
                    <text className="grafico-rotulo" x={c.w / 2} y={c.h - 5} textAnchor="middle" paintOrder="stroke" stroke="#ffffff" strokeWidth="6" strokeLinejoin="round" fill={TEMA.rotulo} fontSize={c.px} fontFamily="ui-monospace, monospace" fontWeight="650"
                      data-x={c.x} data-y={c.y} data-w={c.w} data-h={c.h} data-barra={c.barra} data-serie={c.serie}>{c.texto}</text>
                  </svg>
                </span>
              ))}
            </div>
            {foco != null && (
              <>
                {linha && <i className="grafico-mira" style={{ left: `${x(foco)}%` }} />}
                <div className="grafico-dica" style={{ left: `${x(foco)}%`, transform: `translateX(${x(foco) > 70 ? '-100%' : x(foco) < 30 ? '0' : '-50%'})` }}>
                  <span className="grafico-dica-per">{mesCurto(periodos[foco])}</span>
                  <span><i className="chave a" /><strong>{formato(va.get(periodos[foco]) ?? 0)}</strong> {rotuloA}</span>
                  {b && <span><i className="chave b" /><strong>{formato(vb.get(periodos[foco]) ?? 0)}</strong> {rotuloB}</span>}
                  {temporal && <span><i className="chave tendencia" /><strong>{formato(tend[foco] ?? 0)}</strong> Tendência</span>}
                  {aoAbrir && <span className="grafico-dica-acao">clique para detalhar</span>}
                </div>
              </>
            )}
          </div>
        </div>
        <div className="grafico-x" aria-hidden="true" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
          {periodos.map((p, i) => <span key={p}>{(n - 1 - i) % passoX === 0 ? mesCurto(p) : ''}</span>)}
        </div>
      </div>
      <table className="sr-only">
        <caption>{rotuloMes}</caption>
        <tbody>{periodos.map((p) => <tr key={p}><th scope="row">{mesCurto(p)}</th><td>{formato(va.get(p) ?? 0)}</td>{b && <td>{formato(vb.get(p) ?? 0)}</td>}</tr>)}</tbody>
      </table>
      <FonteRodape fonte={fonte} atualizadoEm={atualizadoEm} />
    </div>
  )
}

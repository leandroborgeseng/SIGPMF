import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { ColunasMes, compacto, num, pct, reais, type Ponto } from './graficos'

type Dim = { chave: string; nome: string; ordem?: string[]; rotulos?: Record<string, string> }
type Medida = { chave: string; nome: string; formato: 'n' | 'brl' | 'pct' | 'dias'; aditiva: boolean }
type Catalogo = Record<string, { dimensoes: Dim[]; medidas: Medida[] }>
type Linha = Record<string, string | number | boolean | null>
type Resp = { linhas: Linha[]; ocultos: number; truncado: boolean; calculado: boolean }
type Passo = { dim: string; valor: string }
export type Fixos = Record<string, string | number>
/** O que um clique abre: o cubo, o recorte já escolhido e a ordem sugerida para descer. */
export type Detalhe = { titulo: string; sub?: string; cubo: string; fixos?: Fixos; caminho: string[]; medidas?: string[]; mesAte?: number; ocultar?: string[] }

const VISIVEIS = 15
const ANO_DO_MES = ['ano', 'ano_venc']
const TEMPO = ['ano', 'ano_venc', 'exercicio', 'mes']
let catalogoLido: Catalogo | null = null
const respOk = (r: unknown): r is Resp => !!r && Array.isArray((r as Resp).linhas)

export const fmtMedida = (v: number | null | undefined, f: Medida['formato']) => {
  if (v == null) return '—'
  if (f === 'brl') return reais(v)
  if (f === 'pct') return pct(v)
  if (f === 'dias') return `${num(v)} d`
  return num(v)
}
const texto = (dim: Dim | undefined, v: unknown) => {
  if (v === true) return 'Sim'
  if (v === false) return 'Não'
  if (v == null) return 'Não informado'
  if (dim?.chave === 'mes') return ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro', '13º'][Number(v) - 1] ?? String(v)
  return dim?.rotulos?.[String(v)] ?? String(v)
}

function baixarCsv(nome: string, colunas: string[], linhas: (string | number | null)[][]) {
  // Texto que começa com = + - @ viraria fórmula na planilha
  const celula = (v: string | number | null) => {
    if (v == null) return ''
    if (typeof v === 'number') return String(v).replace('.', ',')
    return `"${(/^[=+\-@]/.test(v) ? `'${v}` : v).replace(/"/g, '""')}"`
  }
  const csv = [colunas.map(celula), ...linhas.map((l) => l.map(celula))].map((l) => l.join(';')).join('\r\n')
  const url = URL.createObjectURL(new Blob([String.fromCharCode(0xfeff) + csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url; a.download = `${nome}.csv`; a.click()
  URL.revokeObjectURL(url)
}

/** Desce nos dados de um cubo: cada linha abre o próximo nível; a trilha volta, e "Abrir por" troca o ângulo. Só totais. */
export function DrillCubo({ cubo, fixos = {}, caminho, medidas, mesAte, ocultar, nomeArquivo = 'detalhe' }: Omit<Detalhe, 'titulo' | 'sub'> & { nomeArquivo?: string }) {
  const [cat, setCat] = useState<Catalogo | null>(catalogoLido)
  const [falhou, setFalhou] = useState(false)
  const [passos, setPassos] = useState<Passo[]>([])
  const [dimEscolhida, setDimEscolhida] = useState<string | null>(null)
  const [medidaEscolhida, setMedidaEscolhida] = useState<string | null>(null)
  const [r, setR] = useState<(Resp & { dim: string }) | null>(null)  // a resposta guarda a dimensão: enquanto a próxima chega, a tela segue coerente
  const [anterior, setAnterior] = useState<Linha[] | null>(null)
  const [tudo, setTudo] = useState(false)
  const [carregando, setCarregando] = useState(false)
  const [tentativa, setTentativa] = useState(0)

  useEffect(() => {
    if (cat) return
    api<{ catalogo?: Catalogo }>('/cubos').then((x) => {
      if (x?.catalogo && typeof x.catalogo === 'object') { catalogoLido = x.catalogo; setCat(x.catalogo) } else setFalhou(true)
    }).catch(() => setFalhou(true))
  }, [cat, tentativa])

  const c = cat?.[cubo]
  const chaveFixos = JSON.stringify(fixos)
  const filtros = useMemo(() => {
    const f: Fixos = JSON.parse(chaveFixos)
    passos.forEach((p) => { f[p.dim] = p.valor })
    return f
  }, [chaveFixos, passos])
  const temAno = ANO_DO_MES.some((k) => filtros[k] != null)
  const dimensoes = useMemo(() => (c?.dimensoes ?? []).filter((d) => filtros[d.chave] == null && !ocultar?.includes(d.chave) && (d.chave !== 'mes' || temAno)), [c, filtros, temAno, ocultar])
  const proxima = (usadas: Fixos) => caminho.find((k) => usadas[k] == null && dimensoes.some((d) => d.chave === k)) ?? dimensoes[0]?.chave ?? null
  const dim = dimEscolhida && dimensoes.some((d) => d.chave === dimEscolhida) ? dimEscolhida : proxima(filtros)
  const lista = useMemo(() => (c?.medidas ?? []).filter((m) => !medidas || medidas.includes(m.chave)), [c, medidas])
  const medida = lista.find((m) => m.chave === medidaEscolhida) ?? lista[0]
  const anoComparado = ANO_DO_MES.find((k) => filtros[k] != null && dim !== k)

  useEffect(() => {
    if (!c || !dim) { setR(null); return }
    let vale = true
    const query = (f: Fixos) => {
      const q = new URLSearchParams({ agrupar: dim, medidas: lista.map((m) => m.chave).join(',') })
      Object.entries(f).forEach(([k, v]) => q.set(`f_${k}`, String(v)))
      if (mesAte) q.set('mes_ate', String(mesAte))
      return `/cubos/${cubo}?${q}`
    }
    setCarregando(true); setTudo(false)
    api<unknown>(query(filtros)).then((x) => {
      if (!vale) return
      if (respOk(x)) { setR({ ...x, dim }); setFalhou(false) } else setFalhou(true)
    }).catch(() => { if (vale) setFalhou(true) }).finally(() => { if (vale) setCarregando(false) })
    setAnterior(null)
    if (anoComparado) {
      api<unknown>(query({ ...filtros, [anoComparado]: Number(filtros[anoComparado]) - 1 }))
        .then((x) => { if (vale && respOk(x)) setAnterior(x.linhas) }).catch(() => undefined)
    }
    return () => { vale = false }
  }, [c, cubo, dim, filtros, lista, mesAte, anoComparado, tentativa])

  const dimTab = r?.dim ?? dim
  const dimTabInfo = c?.dimensoes.find((d) => d.chave === dimTab)
  const linhas = useMemo(() => {
    const l = [...(r?.linhas ?? [])]
    if (!r || !medida) return l
    if (dimTabInfo?.ordem) { const o = dimTabInfo.ordem; return l.sort((a, b) => o.indexOf(String(a[r.dim])) - o.indexOf(String(b[r.dim]))) }
    if (TEMPO.includes(r.dim)) return l.sort((a, b) => Number(a[r.dim]) - Number(b[r.dim]))
    return l.sort((a, b) => Number(b[medida.chave] ?? -Infinity) - Number(a[medida.chave] ?? -Infinity))
  }, [r, dimTabInfo, medida])
  const prev = useMemo(() => new Map((anterior ?? []).map((l) => [String(l[dimTab ?? '']), l])), [anterior, dimTab])

  if (falhou && !r) {
    return <p className="sutil">Detalhe indisponível no momento. <button type="button" className="link-btn" onClick={() => { setFalhou(false); setTentativa((t) => t + 1) }}>Tentar de novo</button></p>
  }
  if (!c || !medida) return cat ? <p className="sutil">Detalhe indisponível.</p> : <p className="sutil">Carregando…</p>
  if (r && !r.calculado) {
    return <p className="vazio">Os cubos precisam ser calculados para mostrar este detalhe. Um administrador pode fazer isso em <Link to="/painel/cruzamentos">Cruzamentos</Link>.</p>
  }

  const descer = (valor: unknown) => {
    if (!dimTab) return
    const novos = { ...filtros, [dimTab]: String(valor) }
    setPassos((p) => [...p, { dim: dimTab, valor: String(valor) }])
    setDimEscolhida(caminho.find((k) => novos[k] == null && k !== dimTab) ?? null)
  }
  const abertos = tudo ? linhas : linhas.slice(0, VISIVEIS)
  const total = medida.aditiva ? linhas.reduce((s, l) => s + Number(l[medida.chave] ?? 0), 0) : null
  const topo = Math.max(...linhas.map((l) => Number(l[medida.chave] ?? 0)), 1)
  const ehTempo = !!dimTab && TEMPO.includes(dimTab)
  // Linha do tempo: mês precisa do ano filtrado, ano é o próprio período
  const anoDoFiltro = ANO_DO_MES.map((k) => filtros[k]).find((v) => v != null)
  const periodo = (l: Linha) => (dimTab === 'mes' ? `${anoDoFiltro}-${String(l.mes).padStart(2, '0')}` : String(dimTab ? l[dimTab] : ''))
  const serie: Ponto[] = ehTempo ? linhas.map((l) => ({ periodo: periodo(l), dim: '', valor: Number(l[medida.chave] ?? 0) })) : []
  const podeDescer = dimensoes.length > 1
  const csv = () => baixarCsv(nomeArquivo, [dimTabInfo?.nome ?? '', ...lista.map((m) => m.nome)],
    linhas.map((l) => [texto(dimTabInfo, dimTab ? l[dimTab] : null), ...lista.map((m) => (l[m.chave] == null ? null : Number(l[m.chave])))]))
  const nomeDim = (k: string) => c.dimensoes.find((d) => d.chave === k)

  return (
    <div className={`drill${carregando ? ' carregando' : ''}`}>
      <nav className="drill-trilha" aria-label="Níveis abertos">
        <ol>
          <li>{passos.length ? <button type="button" className="link-btn" onClick={() => { setPassos([]); setDimEscolhida(null) }}>Início</button> : <span aria-current="location">Início</span>}</li>
          {passos.map((p, i) => (
            <li key={p.dim}>
              {i === passos.length - 1 ? <span aria-current="location">{nomeDim(p.dim)?.nome}: <strong>{texto(nomeDim(p.dim), p.valor)}</strong></span>
                : <button type="button" className="link-btn" onClick={() => { setPassos(passos.slice(0, i + 1)); setDimEscolhida(null) }}>{nomeDim(p.dim)?.nome}: {texto(nomeDim(p.dim), p.valor)}</button>}
            </li>
          ))}
        </ol>
      </nav>
      {dimensoes.length > 0 && (
        <div className="drill-por" role="group" aria-label="Abrir por">
          <span className="sutil">Abrir por</span>
          {dimensoes.map((d) => <button key={d.chave} type="button" className="chip" aria-pressed={d.chave === dim} onClick={() => setDimEscolhida(d.chave)}>{d.nome}</button>)}
        </div>
      )}
      {!dim ? <p className="sutil">Este é o nível mais detalhado disponível.</p> : !r ? <p className="sutil">Carregando…</p> : linhas.length === 0 ? (
        <p className="vazio">{r.ocultos ? 'Este recorte tem menos de 5 pessoas e não é mostrado, para não identificar ninguém.' : 'Sem dados neste recorte.'}</p>
      ) : (
        <>
          {ehTempo && (
            <ColunasMes a={serie} rotuloA={medida.nome} meses={120} formato={(v) => fmtMedida(v, medida.formato)}
              aoAbrir={podeDescer ? (p) => { const l = linhas.find((x) => periodo(x) === p); if (l && dimTab) descer(l[dimTab]) } : undefined} />
          )}
          <div className="agenda-rolagem">
            <table className="drill-tabela">
              <caption className="sr-only">{medida.nome} por {dimTabInfo?.nome}</caption>
              <thead>
                <tr>
                  <th scope="col">{dimTabInfo?.nome}</th>
                  {!ehTempo && <th scope="col" aria-hidden="true" className="drill-col-barra" />}
                  {lista.map((m) => (
                    <th key={m.chave} scope="col" className="num">
                      {lista.length > 1 ? <button type="button" className={m.chave === medida.chave ? 'drill-med ativa' : 'drill-med'} aria-pressed={m.chave === medida.chave} onClick={() => setMedidaEscolhida(m.chave)}>{m.nome}</button> : m.nome}
                    </th>
                  ))}
                  {total != null && <th scope="col" className="num">% do total</th>}
                  {anoComparado && <th scope="col" className="num">Sobre {Number(filtros[anoComparado]) - 1}</th>}
                </tr>
              </thead>
              <tbody>
                {abertos.map((l) => {
                  const chave = String(dimTab ? l[dimTab] : '')
                  const v = Number(l[medida.chave] ?? 0)
                  const ant = prev.get(chave)?.[medida.chave]
                  const delta = ant != null && Number(ant) !== 0 && l[medida.chave] != null ? ((v - Number(ant)) / Math.abs(Number(ant))) * 100 : null
                  return (
                    <tr key={chave}>
                      <th scope="row">
                        {podeDescer ? <button type="button" className="drill-abrir" onClick={() => descer(l[dimTab!])}>{texto(dimTabInfo, l[dimTab!])}</button> : texto(dimTabInfo, l[dimTab!])}
                      </th>
                      {!ehTempo && <td className="drill-col-barra" aria-hidden="true"><span className="barras-h-trilho"><span className="barras-h-barra" style={{ width: `${Math.max(1, (v / topo) * 100)}%` }} /></span></td>}
                      {lista.map((m) => <td key={m.chave} className={`num${m.chave === medida.chave ? ' forte' : ''}`}>{fmtMedida(l[m.chave] as number | null, m.formato)}</td>)}
                      {total != null && <td className="num sutil">{total ? pct((v / total) * 100) : '—'}</td>}
                      {anoComparado && <td className="num sutil">{delta == null ? '—' : Math.abs(delta) < 0.05 ? '= igual' : `${delta > 0 ? '▲' : '▼'} ${pct(Math.abs(delta))}`}</td>}
                    </tr>
                  )
                })}
              </tbody>
              {linhas.length > 1 && lista.some((m) => m.aditiva) && (
                <tfoot>
                  <tr>
                    <th scope="row">Total mostrado</th>
                    {!ehTempo && <td className="drill-col-barra" aria-hidden="true" />}
                    {lista.map((m) => <td key={m.chave} className="num">{m.aditiva ? fmtMedida(linhas.reduce((s, l) => s + Number(l[m.chave] ?? 0), 0), m.formato) : ''}</td>)}
                    {total != null && <td />}
                    {anoComparado && <td />}
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
          <div className="drill-rodape">
            {linhas.length > VISIVEIS && <button type="button" className="btn secundario" onClick={() => setTudo((t) => !t)}>{tudo ? `Mostrar só os ${VISIVEIS} primeiros` : `Mostrar todos (${compacto(linhas.length)})`}</button>}
            <button type="button" className="btn secundario" onClick={csv}>Baixar CSV</button>
            {r.ocultos > 0 && <span className="sutil">{num(r.ocultos)} {r.ocultos === 1 ? 'grupo oculto' : 'grupos ocultos'} por ter menos de 5 pessoas.</span>}
            {r.truncado && <span className="sutil">Mostrando as primeiras 5.000 linhas.</span>}
          </div>
        </>
      )}
    </div>
  )
}

/** Painel lateral sobre a página. Esc fecha; o foco volta para onde estava. */
export function Gaveta({ titulo, sub, aoFechar, children }: { titulo: string; sub?: string; aoFechar: () => void; children: ReactNode }) {
  const caixa = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const antes = document.activeElement as HTMLElement | null
    caixa.current?.querySelector<HTMLElement>('.gaveta-fechar')?.focus()
    return () => antes?.focus?.()
  }, [])
  const teclas = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.stopPropagation(); aoFechar(); return }
    if (e.key !== 'Tab') return
    const f = [...(caixa.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input, select, [tabindex="0"]') ?? [])]
    if (!f.length) return
    const prim = f[0], ult = f[f.length - 1]
    if (e.shiftKey && document.activeElement === prim) { e.preventDefault(); ult.focus() } else if (!e.shiftKey && document.activeElement === ult) { e.preventDefault(); prim.focus() }
  }
  return (
    <div className="gaveta-fundo" onMouseDown={(e) => { if (e.target === e.currentTarget) aoFechar() }}>
      <div className="gaveta" role="dialog" aria-modal="true" aria-labelledby="gaveta-titulo" ref={caixa} onKeyDown={teclas}>
        <header className="gaveta-cab">
          <div>
            <h2 id="gaveta-titulo">{titulo}</h2>
            {sub && <p className="sutil">{sub}</p>}
          </div>
          <button type="button" className="icone-btn gaveta-fechar" aria-label="Fechar detalhe" onClick={aoFechar}>×</button>
        </header>
        <div className="gaveta-corpo">{children}</div>
      </div>
    </div>
  )
}

/** Abre o detalhe de qualquer gráfico da página: `abrir({...})` e `{gaveta}` no fim do JSX. */
export function useDetalhe() {
  const [d, setD] = useState<Detalhe | null>(null)
  const abrir = useCallback((x: Detalhe) => setD(x), [])
  const gaveta = d && (
    <Gaveta titulo={d.titulo} sub={d.sub} aoFechar={() => setD(null)}>
      <DrillCubo key={JSON.stringify(d)} cubo={d.cubo} fixos={d.fixos} caminho={d.caminho} medidas={d.medidas} mesAte={d.mesAte} ocultar={d.ocultar} nomeArquivo={d.titulo} />
    </Gaveta>
  )
  return { abrir, gaveta }
}

/** "2025-03" -> { ano: 2025, mes: 3 }. */
export const doPeriodo = (p: string, chaveAno = 'ano'): Fixos => {
  const [a, m] = p.split('-')
  return m ? { [chaveAno]: Number(a), mes: Number(m) } : { [chaveAno]: Number(a) }
}

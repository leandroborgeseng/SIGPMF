import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiErro } from '../api'
import { useAuth } from '../auth'
import { Erro, Esqueleto } from '../componentes'
import { DrillCubo, Gaveta } from './Drill'
import { Cabecalho, Painel, num, pct, reais } from './graficos'

type Formato = 'n' | 'pct' | 'brl' | 'pp'
type Medida = { chave: string; nome: string; formato: Formato; bom: 'alto' | 'baixo' | null }
type Bairro = { bairro_id: number; bairro: string } & Record<string, number | string | null>
type Destaque = { bairro: string; medida: string; valor: number; z: number; sinal: 'bom' | 'ruim' | null; frase: string }
type Correlacao = { a: string; b: string; r: number; bairros: number; frase: string }
type Achado = { id: string; titulo: string; valor: number; formato: Formato; detalhe: string; areas: string[]; acao: string | null }
type Resp = { calculado: boolean; gerado_em?: string; medidas: Medida[]; bairros: Bairro[]; destaques: Destaque[]; correlacoes: Correlacao[]; cidade: Achado[] }
type Estado = { em_andamento: { passo: string | null } | null }

const valida = (r: unknown): r is Resp => !!r && typeof r === 'object' && Array.isArray((r as Resp).bairros) && Array.isArray((r as Resp).cidade)
const fmt = (v: number | null | undefined, f: Formato) => {
  if (v == null) return '—'
  if (f === 'pct') return pct(v)
  if (f === 'brl') return reais(v)
  if (f === 'pp') return `${v > 0 ? '+' : ''}${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(v)} p.p.`
  return Math.abs(v) >= 100 ? num(v) : new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(v)
}
// A tabela de bairros mostra um conjunto de medidas por vez
const CONJUNTOS: { nome: string; colunas: string[] }[] = [
  { nome: 'Visão geral', colunas: ['pessoas', 'pct_idosos', 'consultas_mil', 'imoveis', 'inadimplencia', 'divida_imovel', 'empresas'] },
  { nome: 'Saúde', colunas: ['pessoas', 'consultas_mil', 'espera_media', 'pct_fora_bairro', 'pct_idosos_sem_consulta', 'vacinas_mil', 'pct_criancas_sem_vacina'] },
  { nome: 'Tributos', colunas: ['imoveis', 'iptu_medio', 'inadimplencia', 'divida_total', 'divida_imovel', 'pct_protestada'] },
  { nome: 'Pessoas e educação', colunas: ['pessoas', 'pct_idosos', 'pct_criancas', 'alunos_mil'] },
  { nome: 'Empresas', colunas: ['empresas', 'empresas_mil', 'pct_empresas_debito'] },
]
const VISIVEIS = 30
// Abas da ficha do bairro: o mesmo drill-down dos painéis, já filtrado
const ABAS: { nome: string; cubo: string; fixos?: Record<string, string>; caminho: string[]; medidas?: string[]; ocultar?: string[] }[] = [
  { nome: 'Saúde', cubo: 'saude', fixos: { tipo: 'consulta' }, caminho: ['especialidade', 'unidade', 'faixa', 'ano'], medidas: ['eventos'], ocultar: ['vacina'] },
  { nome: 'Tributos', cubo: 'tributario', caminho: ['tributo', 'exercicio'], medidas: ['lancado', 'pago', 'vencido_aberto', 'inadimplencia_pct'] },
  { nome: 'Dívida ativa', cubo: 'divida', caminho: ['natureza', 'faixa_saldo', 'ano'], medidas: ['saldo', 'inscricoes'] },
  { nome: 'Imóveis', cubo: 'imoveis', caminho: ['situacao'], medidas: ['imoveis', 'iptu_anual', 'com_possuidor'] },
  { nome: 'Empresas', cubo: 'empresas', caminho: ['tipo_pessoa', 'possui_debito'] },
  { nome: 'Educação', cubo: 'educacao', fixos: { ativo: 'true' }, caminho: ['escola', 'faixa'] },
  { nome: 'Moradores', cubo: 'pessoas', fixos: { falecido: 'false' }, caminho: ['faixa', 'consultou_12m', 'vacinou_24m'] },
]

function Ficha({ b, medidas, bairros }: { b: Bairro; medidas: Medida[]; bairros: Bairro[] }) {
  const [aba, setAba] = useState(0)
  const a = ABAS[aba]
  const comValor = medidas.filter((m) => b[m.chave] != null)
  return (
    <>
      <section className="cartao painel-bloco" aria-label="Medidas do bairro">
        <div className="painel-bloco-cab"><h2>Comparado aos outros bairros</h2><span className="sutil">a marca escura é a média dos bairros</span></div>
        <div className="cz-ficha">
          {comValor.map((m) => {
            const vs = bairros.map((x) => x[m.chave]).filter((v): v is number => typeof v === 'number')
            const topo = Math.max(...vs, 1)
            const media = vs.reduce((s, v) => s + v, 0) / (vs.length || 1)
            return (
              <div key={m.chave} className="cz-ficha-linha">
                <span>{m.nome}</span>
                <span className="v">{fmt(b[m.chave] as number, m.formato)}</span>
                <span className="cz-medidor" aria-hidden="true"><b style={{ width: `${(Number(b[m.chave]) / topo) * 100}%` }} /><i style={{ left: `${(media / topo) * 100}%` }} /></span>
                <span className="m">média {fmt(media, m.formato)}</span>
              </div>
            )
          })}
        </div>
      </section>
      <section className="cartao painel-bloco" aria-label="Detalhe do bairro">
        <div className="cz-conjuntos" role="group" aria-label="Área">
          {ABAS.map((x, i) => <button key={x.nome} type="button" className="chip" aria-pressed={i === aba} onClick={() => setAba(i)}>{x.nome}</button>)}
        </div>
        <DrillCubo key={a.nome} cubo={a.cubo} fixos={{ ...a.fixos, bairro: b.bairro }} caminho={a.caminho} medidas={a.medidas} ocultar={a.ocultar} nomeArquivo={`${b.bairro}-${a.nome}`} />
      </section>
    </>
  )
}

/** Dispersão entre os bairros: cada ponto é um bairro; clique abre a ficha. O teclado usa a tabela de bairros. */
function Dispersao({ x, y, bairros, aoAbrir }: { x: Medida; y: Medida; bairros: Bairro[]; aoAbrir: (b: Bairro) => void }) {
  const [dica, setDica] = useState<Bairro | null>(null)
  const pts = bairros.filter((b) => typeof b[x.chave] === 'number' && typeof b[y.chave] === 'number')
  const faixa = (k: string) => {
    const v = pts.map((b) => Number(b[k]))
    const min = Math.min(...v), max = Math.max(...v)
    return { min, max: max === min ? min + 1 : max }
  }
  const fx = faixa(x.chave), fy = faixa(y.chave)
  const px = (b: Bairro) => ((Number(b[x.chave]) - fx.min) / (fx.max - fx.min)) * 96 + 2
  const py = (b: Bairro) => 98 - ((Number(b[y.chave]) - fy.min) / (fy.max - fy.min)) * 96
  return (
    <div className="cz-disp" onMouseLeave={() => setDica(null)}>
      <div className="cz-disp-y" aria-hidden="true"><span>{fmt(fy.max, y.formato)}</span><span>{fmt(fy.min, y.formato)}</span></div>
      <div className="cz-disp-x" aria-hidden="true"><span>{fmt(fx.min, x.formato)}</span><span>{x.nome} →</span><span>{fmt(fx.max, x.formato)}</span></div>
      <span className="cz-disp-titulo-y" aria-hidden="true">↑ {y.nome}</span>
      {pts.map((b) => (
        <button key={b.bairro_id} type="button" className="cz-disp-ponto" tabIndex={-1} aria-hidden="true" style={{ left: `${px(b)}%`, top: `${py(b)}%` }}
          onMouseEnter={() => setDica(b)} onClick={() => aoAbrir(b)} />
      ))}
      {dica && (
        <div className="cz-disp-dica" style={{ left: `${px(dica)}%`, top: `${py(dica)}%` }}>
          <strong>{dica.bairro}</strong> · {fmt(dica[x.chave] as number, x.formato)} · {fmt(dica[y.chave] as number, y.formato)}
        </div>
      )}
    </div>
  )
}

/** Cruzamentos entre as áreas: achados da cidade, bairros fora da curva, correlações e a matriz por bairro. Só agregados. */
export function PainelCruzamentos() {
  const { sessao } = useAuth()
  const ehAdmin = sessao?.perfil === 'admin'
  const [d, setD] = useState<Resp | null>(null)
  const [falhou, setFalhou] = useState(false)
  const [calculando, setCalculando] = useState(false)
  const [aviso, setAviso] = useState<string | null>(null)
  const [ordem, setOrdem] = useState<{ coluna: string; desc: boolean }>({ coluna: 'pessoas', desc: true })
  const [busca, setBusca] = useState('')
  const [conjunto, setConjunto] = useState(0)
  const [todos, setTodos] = useState(false)
  const [par, setPar] = useState(0)
  const [ficha, setFicha] = useState<Bairro | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const carregar = useCallback(() => {
    setFalhou(false)
    api<unknown>('/cruzamentos').then((r) => (valida(r) ? setD(r) : setFalhou(true))).catch(() => setFalhou(true))
  }, [])
  const acompanhar = useCallback(() => {
    api<Estado>('/cubos').then((e) => {
      if (e?.em_andamento) { setCalculando(true); timer.current = setTimeout(acompanhar, 5000) } else { setCalculando((era) => { if (era) carregar(); return false }) }
    }).catch(() => setCalculando(false))
  }, [carregar])
  useEffect(() => { carregar(); acompanhar(); return () => { if (timer.current) clearTimeout(timer.current) } }, [carregar, acompanhar])

  const recalcular = async () => {
    setAviso(null)
    try {
      await api('/cubos/recalcular', { method: 'POST' })
      setCalculando(true); timer.current = setTimeout(acompanhar, 3000)
    } catch (er) {
      setAviso(er instanceof ApiErro ? er.message : 'Não foi possível iniciar o recálculo.')
    }
  }

  const medidas = useMemo(() => Object.fromEntries((d?.medidas ?? []).map((m) => [m.chave, m])), [d])
  const colunas = CONJUNTOS[conjunto].colunas.filter((c) => medidas[c])
  const linhas = useMemo(() => {
    const b = busca.trim().toLowerCase()
    const l = (d?.bairros ?? []).filter((x) => !b || x.bairro.toLowerCase().includes(b))
    const v = (x: Bairro) => x[ordem.coluna]
    return [...l].sort((p, q) => {
      const a = v(p), c = v(q)
      if (a == null || c == null) return a == null ? (c == null ? 0 : 1) : -1  // sem valor sempre por último
      const r = typeof a === 'string' ? String(a).localeCompare(String(c), 'pt-BR') : Number(a) - Number(c)
      return ordem.desc ? -r : r
    })
  }, [d, ordem, busca])
  // Tom de uma medida numa coluna: um só azul, do menor ao maior entre os bairros
  const faixas = useMemo(() => Object.fromEntries(colunas.map((c) => {
    const v = (d?.bairros ?? []).map((b) => b[c]).filter((x): x is number => typeof x === 'number')
    return [c, { min: Math.min(...v), max: Math.max(...v) }]
  })), [conjunto, d]) // eslint-disable-line react-hooks/exhaustive-deps
  const tom = (c: string, v: unknown) => {
    const f = faixas[c]
    if (typeof v !== 'number' || !f || f.max === f.min) return undefined
    return { background: `rgba(0, 102, 204, ${(((v - f.min) / (f.max - f.min)) * 0.3).toFixed(3)})` }
  }
  const clica = (coluna: string) => setOrdem((o) => ({ coluna, desc: o.coluna === coluna ? !o.desc : coluna !== 'bairro' }))
  const seta = (c: string) => (ordem.coluna === c ? (ordem.desc ? ' ▼' : ' ▲') : '')
  const aria = (c: string) => (ordem.coluna === c ? (ordem.desc ? 'descending' : 'ascending') : 'none')
  const porNome = (nome: string) => d?.bairros.find((b) => b.bairro === nome) ?? null
  const correlacao = d?.correlacoes[par]
  const botaoRecalcular = ehAdmin && (
    <div className="acoes">
      <button className="btn secundario" type="button" onClick={recalcular} disabled={calculando}>{calculando ? 'Recalculando…' : 'Recalcular cubos'}</button>
      <span className="sutil" aria-live="polite">{aviso ?? (calculando ? 'Pode levar alguns minutos; a tela atualiza sozinha.' : 'Depois de ligar na agenda, roda toda noite após as cargas.')}</span>
    </div>
  )

  return (
    <section aria-label="Cruzamentos" className="painel painel-financas">
      <Cabecalho kicker="Gestão" titulo="Cruzamentos" atualizado={d?.gerado_em}
        sub="O que aparece ao juntar as bases da prefeitura: achados da cidade, bairros fora da curva e o que anda junto. Só totais, nenhuma pessoa." />
      {falhou ? <Erro texto="Não foi possível carregar os cruzamentos. Tente novamente em instantes." onRetry={carregar} />
        : !d ? <Esqueleto linhas={4} />
        : !d.calculado ? (
          <>
            <p className="vazio">Os cubos ainda não foram calculados.{ehAdmin ? ' Use o botão abaixo para o primeiro cálculo.' : ' Peça a um administrador para fazer o primeiro cálculo.'}</p>
            {botaoRecalcular}
          </>
        ) : (
          <>
            {botaoRecalcular}
            <div className="painel-grade">
              <Painel titulo="Achados da cidade" nota="cruzamentos entre áreas; indicam onde olhar, não são conclusão" largo>
                {d.cidade.length === 0 ? <p className="vazio">Nenhum achado com os dados carregados.</p> : (
                  <ul className="cz-achados">
                    {d.cidade.map((a) => (
                      <li key={a.id} className="cartao">
                        <span className="cartao-rotulo">{a.titulo}</span>
                        <strong className="cz-valor">{fmt(a.valor, a.formato)}</strong>
                        <p className="sutil">{a.detalhe}</p>
                        {a.acao && <p className="cz-acao">{a.acao}</p>}
                        <span className="sutil cz-areas">{a.areas.join(' × ')}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Painel>
              <Painel titulo="Bairros fora da curva" nota="a pelo menos 2 desvios da média dos bairros · clique para abrir o bairro" largo>
                {d.destaques.length === 0 ? <p className="vazio">Nenhum bairro muito fora da média.</p> : (
                  <ul className="cz-destaques">
                    {d.destaques.map((x) => (
                      <li key={`${x.bairro}-${x.medida}`}>
                        <button type="button" onClick={() => setFicha(porNome(x.bairro))}>
                          <strong>{x.bairro}</strong>
                          {x.sinal && <span className={`selo ${x.sinal === 'bom' ? 'sucesso' : 'falha'}`}><i aria-hidden="true" />{x.sinal === 'bom' ? 'Melhor' : 'Atenção'}</span>}
                          <span className="sutil">{x.frase}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </Painel>
              <Painel titulo="O que anda junto" nota="cada ponto é um bairro; correlação não é causa · clique num ponto para abrir o bairro" largo>
                {!correlacao ? <p className="vazio">Nenhuma relação forte com os dados carregados.</p> : (
                  <div className="cz-grupo">
                    <ul className="cz-pares">
                      {d.correlacoes.map((c, i) => (
                        <li key={`${c.a}-${c.b}`}>
                          <button type="button" aria-pressed={i === par} onClick={() => setPar(i)}>
                            {c.frase}<span className="sutil">r = {c.r.toLocaleString('pt-BR')} em {c.bairros} bairros</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                    {medidas[correlacao.a] && medidas[correlacao.b] && <Dispersao x={medidas[correlacao.a]} y={medidas[correlacao.b]} bairros={d.bairros} aoAbrir={setFicha} />}
                  </div>
                )}
              </Painel>
              <Painel titulo="Bairros lado a lado" nota={`${d.bairros.length} bairros · mais escuro = maior na coluna · clique no bairro para abrir`} largo>
                <div className="cz-topo">
                  <div className="cz-conjuntos" role="group" aria-label="Conjunto de medidas">
                    {CONJUNTOS.map((c, i) => <button key={c.nome} type="button" className="chip" aria-pressed={i === conjunto} onClick={() => setConjunto(i)}>{c.nome}</button>)}
                  </div>
                  <label className="cz-busca">Buscar bairro <input type="search" value={busca} onChange={(e) => setBusca(e.target.value)} /></label>
                </div>
                <div className="agenda-rolagem">
                  <table className="drill-tabela cz-calor">
                    <caption className="sr-only">Medidas por bairro</caption>
                    <thead>
                      <tr>
                        <th scope="col" aria-sort={aria('bairro')}><button type="button" className="drill-med" onClick={() => clica('bairro')}>Bairro{seta('bairro')}</button></th>
                        {colunas.map((c) => <th scope="col" key={c} className="num" aria-sort={aria(c)}><button type="button" className="drill-med" onClick={() => clica(c)}>{medidas[c].nome}{seta(c)}</button></th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {(todos ? linhas : linhas.slice(0, VISIVEIS)).map((b) => (
                        <tr key={b.bairro_id}>
                          <th scope="row"><button type="button" className="drill-abrir" onClick={() => setFicha(b)}>{b.bairro}</button></th>
                          {colunas.map((c) => <td key={c} className="num" style={tom(c, b[c])}>{fmt(b[c] as number | null, medidas[c].formato)}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {linhas.length > VISIVEIS && <div className="drill-rodape"><button type="button" className="btn secundario" onClick={() => setTodos((t) => !t)}>{todos ? `Mostrar só os ${VISIVEIS} primeiros` : `Mostrar todos (${linhas.length})`}</button></div>}
              </Painel>
            </div>
            <p className="sutil">Bairro por nome normalizado (Jd. = Jardim). Pessoas: endereço mais recente; imóveis e tributos: bairro do imóvel. Taxas só aparecem em bairro com base suficiente, e grupos com menos de 5 pessoas não aparecem. Servidores por bairro depende do endereço do Senior.</p>
            {ficha && (
              <Gaveta titulo={ficha.bairro} sub="Ficha do bairro: só totais" aoFechar={() => setFicha(null)}>
                <Ficha b={ficha} medidas={d.medidas} bairros={d.bairros} />
              </Gaveta>
            )}
          </>
        )}
    </section>
  )
}

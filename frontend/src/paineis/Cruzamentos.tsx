import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiErro } from '../api'
import { useAuth } from '../auth'
import { Erro, Esqueleto } from '../componentes'
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
// Colunas da tabela de bairros: as demais ficam na escolha de medida
const COLUNAS_PADRAO = ['pessoas', 'pct_idosos', 'consultas_mil', 'pct_idosos_sem_consulta', 'imoveis', 'inadimplencia', 'divida_imovel', 'empresas', 'pct_empresas_debito']

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
  const colunas = COLUNAS_PADRAO.filter((c) => medidas[c])
  const clica = (coluna: string) => setOrdem((o) => ({ coluna, desc: o.coluna === coluna ? !o.desc : coluna !== 'bairro' }))
  const seta = (c: string) => (ordem.coluna === c ? (ordem.desc ? ' ▼' : ' ▲') : '')
  const aria = (c: string) => (ordem.coluna === c ? (ordem.desc ? 'descending' : 'ascending') : 'none')
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
              <Painel titulo="Bairros fora da curva" nota="a pelo menos 2 desvios da média dos bairros">
                {d.destaques.length === 0 ? <p className="vazio">Nenhum bairro muito fora da média.</p> : (
                  <ul className="cz-lista">
                    {d.destaques.map((x) => (
                      <li key={`${x.bairro}-${x.medida}`}><strong>{x.bairro}</strong>{x.sinal && <span className={`selo ${x.sinal === 'bom' ? 'sucesso' : 'falha'}`}>{x.sinal === 'bom' ? 'melhor' : 'atenção'}</span>}<br /><span className="sutil">{x.frase}</span></li>
                    ))}
                  </ul>
                )}
              </Painel>
              <Painel titulo="O que anda junto" nota="correlação entre bairros; não é causa">
                {d.correlacoes.length === 0 ? <p className="vazio">Nenhuma relação forte com os dados carregados.</p> : (
                  <ul className="cz-lista">
                    {d.correlacoes.map((c) => <li key={`${c.a}-${c.b}`}>{c.frase}<br /><span className="sutil">r = {c.r.toLocaleString('pt-BR')} em {c.bairros} bairros</span></li>)}
                  </ul>
                )}
              </Painel>
              <Painel titulo="Bairros lado a lado" nota={`${d.bairros.length} bairros; clique no título para ordenar`} largo>
                <label className="cz-busca">Buscar bairro <input type="search" value={busca} onChange={(e) => setBusca(e.target.value)} /></label>
                <div className="agenda-rolagem">
                  <table className="tabela cz-tabela">
                    <caption className="sr-only">Medidas por bairro</caption>
                    <thead>
                      <tr>
                        <th scope="col" aria-sort={aria('bairro')}><button type="button" className="link-btn" onClick={() => clica('bairro')}>Bairro{seta('bairro')}</button></th>
                        {colunas.map((c) => <th scope="col" key={c} className="num" aria-sort={aria(c)}><button type="button" className="link-btn" onClick={() => clica(c)}>{medidas[c].nome}{seta(c)}</button></th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {linhas.map((b) => (
                        <tr key={b.bairro_id}><th scope="row">{b.bairro}</th>{colunas.map((c) => <td key={c} className="num" data-rotulo={medidas[c].nome}>{fmt(b[c] as number | null, medidas[c].formato)}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Painel>
            </div>
            <p className="sutil">Bairro por nome normalizado (Jd. = Jardim). Pessoas: endereço mais recente; imóveis e tributos: bairro do imóvel. Taxas só aparecem em bairro com base suficiente, e grupos com menos de 5 pessoas não aparecem. Servidores por bairro depende do endereço do Senior.</p>
          </>
        )}
    </section>
  )
}

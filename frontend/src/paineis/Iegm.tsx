import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, dataHoraBR } from '../api'
import { Erro, Esqueleto } from '../componentes'
import { Cabecalho, Kpi, Painel, pct } from './graficos'

type Ponto = { ano_base: number; faixa: string | null }
type Indice = { codigo: string; nome: string; area: string; descricao: string; serie: Ponto[]; atual: string | null; anterior: string | null
  tendencia: 'subiu' | 'caiu' | 'igual' | null; estado_ultimo: Record<string, number>; estado_total: number
  pct_melhor: number | null; pct_mesma: number | null }
type Resp = { anos: number[]; apuracao?: Record<string, number>; ultimo: number | null; indices: Indice[]; faixas: Record<string, string>; carregado_em?: string }

export const FAIXAS = ['A', 'B+', 'B', 'C+', 'C']
const classe = (f: string | null) => (f ? `faixa faixa-${f.replace('+', 'mais')}` : 'faixa faixa-vazia')
const SETA = { subiu: '▲ subiu', caiu: '▼ caiu', igual: '= igual' }
const valida = (r: unknown): r is Resp => !!r && typeof r === 'object' && Array.isArray((r as Resp).anos) && Array.isArray((r as Resp).indices)

/** Selo de faixa do IEG-M (A … C), com o nome da faixa no title e no texto para leitor de tela. */
export function Faixa({ f, nomes }: { f: string | null; nomes: Record<string, string> }) {
  return f ? <span className={classe(f)} title={nomes[f]}>{f}<span className="sr-only"> ({nomes[f]})</span></span>
    : <span className={classe(null)} title="sem nota">—<span className="sr-only"> sem nota</span></span>
}

/** Painel IEG-M (TCE-SP): faixa de cada índice (área da prefeitura) ano a ano e a posição de Franca no estado. */
export function PainelIegm() {
  const [d, setD] = useState<Resp | null>(null)
  const [falhou, setFalhou] = useState(false)
  const carregar = useCallback(() => {
    setFalhou(false)
    api<unknown>('/iegm/painel').then((r) => (valida(r) ? setD(r) : setFalhou(true))).catch(() => setFalhou(true))
  }, [])
  useEffect(carregar, [carregar])

  const geral = d?.indices.find((i) => i.codigo === 'IEG-M')
  const temas = d?.indices.filter((i) => i.codigo !== 'IEG-M') ?? []
  const subiram = temas.filter((i) => i.tendencia === 'subiu').map((i) => i.nome)
  const cairam = temas.filter((i) => i.tendencia === 'caiu').map((i) => i.nome)
  return (
    <section aria-label="IEG-M" className="painel painel-iegm">
      <Cabecalho kicker="Gestão" titulo="IEG-M" atualizado={d?.carregado_em}
        sub="Índice de Efetividade da Gestão Municipal do Tribunal de Contas (TCE-SP): uma faixa por área, de A (altamente efetiva) a C." />
      {falhou ? <Erro texto="Não foi possível carregar o IEG-M. Tente novamente em instantes." onRetry={carregar} />
        : !d ? <Esqueleto linhas={4} />
        : !d.ultimo || !geral ? (
          <p className="vazio">Ainda não há dados do IEG-M. Envie o arquivo em <Link to="/conectores#fonte-iegm">Conectores → IEG-M (TCE-SP)</Link>.</p>
        ) : (
          <>
            <p className="sutil fin-aviso">Ano base {d.ultimo}, publicado pelo TCE em {d.apuracao?.[String(d.ultimo)] ?? d.ultimo + 1}. O TCE avalia o ano anterior ao da publicação.</p>
            <div className="kpis">
              <Kpi rotulo={`IEG-M ${d.ultimo}`} valor={geral.atual ?? '—'} nota={geral.atual ? d.faixas[geral.atual] : undefined}
                tom={geral.atual && ['A', 'B+'].includes(geral.atual) ? 'bom' : geral.atual === 'C' ? 'alerta' : 'normal'} />
              <Kpi rotulo={`Em relação a ${d.ultimo - 1}`} valor={geral.tendencia ? SETA[geral.tendencia] : '—'} nota={geral.anterior ? `era ${geral.anterior}` : undefined} />
              <Kpi rotulo="Municípios do estado em faixa melhor" valor={pct(geral.pct_melhor)} nota={`${pct(geral.pct_mesma)} na mesma faixa (${geral.atual})`} />
              <Kpi rotulo={`Áreas que subiram em ${d.ultimo}`} valor={String(subiram.length)} nota={subiram.join(', ') || 'nenhuma'} tom={subiram.length ? 'bom' : 'normal'} />
              <Kpi rotulo={`Áreas que caíram em ${d.ultimo}`} valor={String(cairam.length)} nota={cairam.join(', ') || 'nenhuma'} tom={cairam.length ? 'alerta' : 'normal'} />
            </div>

            <div className="painel-grade">
              <Painel titulo="Evolução por área" nota={`ano base ${d.anos[0]} a ${d.ultimo}`} largo>
                <div className="agenda-rolagem">
                  <table className="tabela iegm-matriz">
                    <caption className="sr-only">Faixa do IEG-M por área e ano base</caption>
                    <thead><tr><th scope="col">Área</th>{d.anos.map((a) => <th scope="col" key={a} className="num">{a}</th>)}</tr></thead>
                    <tbody>
                      {d.indices.map((i) => (
                        <tr key={i.codigo} className={i.codigo === 'IEG-M' ? 'iegm-geral' : undefined}>
                          <th scope="row"><span className="iegm-nome">{i.nome}</span><span className="sutil iegm-area">{i.codigo} · {i.area}</span></th>
                          {i.serie.map((p) => <td key={p.ano_base}><Faixa f={p.faixa} nomes={d.faixas} /></td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <ul className="iegm-legenda" aria-label="Faixas do IEG-M">
                  {FAIXAS.map((f) => <li key={f}><Faixa f={f} nomes={d.faixas} /> {d.faixas[f]}</li>)}
                </ul>
              </Painel>
              <Painel titulo={`Franca × municípios do estado em ${d.ultimo}`} nota="quantos municípios ficaram em cada faixa; a de Franca em destaque" largo>
                <ul className="iegm-estado">
                  {d.indices.map((i) => (
                    <li key={i.codigo}>
                      <span className="iegm-estado-nome">{i.nome}</span>
                      <span className="iegm-barra" role="img"
                        aria-label={`${i.nome}: ${FAIXAS.map((f) => `${f} ${i.estado_ultimo[f] ?? 0}`).join(', ')} municípios; Franca ${i.atual ?? 'sem nota'}`}>
                        {FAIXAS.map((f) => (i.estado_ultimo[f] ?? 0) > 0 && (
                          <span key={f} className={`${classe(f)} seg${f === i.atual ? ' franca' : ''}`}
                            style={{ flexGrow: i.estado_ultimo[f] }} title={`${f}: ${i.estado_ultimo[f]} municípios${f === i.atual ? ' (Franca)' : ''}`}>
                            {f === i.atual ? `Franca ${f}` : ''}
                          </span>
                        ))}
                      </span>
                      <span className="sutil iegm-estado-pct">{i.pct_melhor != null ? `${pct(i.pct_melhor)} acima` : '—'}</span>
                    </li>
                  ))}
                </ul>
              </Painel>
              <Painel titulo="O que cada área mede" largo>
                <dl className="iegm-desc">
                  {temas.map((i) => <div key={i.codigo}><dt>{i.nome} <span className="sutil">({i.codigo} · {i.area})</span></dt><dd>{i.descricao}</dd></div>)}
                </dl>
              </Painel>
            </div>
            <p className="sutil">Fonte: painel do IEG-M do TCE-SP (painel.tce.sp.gov.br). Carregado no SIGPMF em {dataHoraBR(d.carregado_em)}. A nota numérica não é publicada: só a faixa.</p>
          </>
        )}
    </section>
  )
}

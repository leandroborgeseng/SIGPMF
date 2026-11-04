import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, dataHoraBR } from '../api'
import { Erro, Esqueleto } from '../componentes'
import { BarrasH, Cabecalho, ColunasMes, Kpi, Painel, num, pct, type Ponto } from './graficos'

type Atual = { beneficiarios: number; odonto: number; populacao: number | null; taxa: number | null; taxa_anterior: number | null; beneficiarios_anterior: number | null; aderidos: number; cancelados: number }
type Bairro = { bairro: string; pessoas: number; taxa_estimada: number }
type Resp = { atual: Atual | null; carregado_em?: string | null; competencia?: { ano: number; mes: number }; estado?: number | null; posicao?: number | null; total_pares?: number
  cobertura_mes?: Ponto[]; beneficiarios_mes?: Ponto[]; taxa_faixa?: Ponto[]; operadoras?: Ponto[]; contratos?: Ponto[]; cidades?: Ponto[]; bairros?: Bairro[] }
const valida = (r: unknown): r is Resp => !!r && typeof r === 'object' && 'atual' in r
const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
const VISIVEIS = 20

/** Cobertura de planos de saúde em Franca: ANS ÷ IBGE. O número por bairro é estimativa, e a tela diz isso. */
export function PainelCobertura() {
  const [d, setD] = useState<Resp | null>(null)
  const [falhou, setFalhou] = useState(false)
  const [todos, setTodos] = useState(false)
  const carregar = useCallback(() => {
    setFalhou(false)
    api<unknown>('/cobertura/painel').then((r) => (valida(r) ? setD(r) : setFalhou(true))).catch(() => setFalhou(true))
  }, [])
  useEffect(carregar, [carregar])
  const a = d?.atual
  const c = d?.competencia
  const delta = a?.taxa != null && a.taxa_anterior != null ? a.taxa - a.taxa_anterior : null
  const fmtPp = (v: number) => Math.abs(v) < 0.05 ? 'estável em 12 meses' : `${v > 0 ? '▲' : '▼'} ${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(Math.abs(v))} p.p. em 12 meses`
  const bairros = useMemo(() => d?.bairros ?? [], [d])
  const topo = Math.max(...bairros.map((b) => b.taxa_estimada), 1)
  return (
    <section aria-label="Cobertura de planos" className="painel painel-financas">
      <Cabecalho kicker="Gestão" titulo="Cobertura de planos de saúde" atualizado={d?.carregado_em}
        sub="Quantas pessoas de Franca têm plano médico-hospitalar: beneficiários da ANS sobre a população do IBGE. Nenhuma pessoa é identificada." />
      {falhou ? <Erro texto="Não foi possível carregar a cobertura. Tente novamente em instantes." onRetry={carregar} />
        : !d ? <Esqueleto linhas={4} />
        : !a || !c ? <p className="vazio">Ainda não há dados da ANS. Carregue em <Link to="/conectores#fonte-ans">Conectores → ANS e IBGE</Link>.</p>
        : (
          <>
            <p className="sutil fin-aviso">Dados de {MESES[c.mes - 1]}/{c.ano}. A ANS conta vínculos, não pessoas: quem tem dois planos conta duas vezes, então a cobertura real de pessoas pode ser um pouco menor.</p>
            <div className="kpis">
              <Kpi rotulo="Cobertura médico-hospitalar" valor={a.taxa != null ? pct(a.taxa) : '—'} nota={delta != null ? fmtPp(delta) : 'população do IBGE ainda não carregada'} mini={d.cobertura_mes?.slice(-24).map((p) => p.valor)} />
              <Kpi rotulo="Beneficiários em plano médico" valor={num(a.beneficiarios)} nota={a.populacao ? `de ${num(a.populacao)} habitantes` : undefined} mini={d.beneficiarios_mes?.slice(-24).map((p) => p.valor)} />
              <Kpi rotulo="Sem plano médico (estimativa)" valor={a.populacao ? num(Math.max(a.populacao - a.beneficiarios, 0)) : '—'} nota="no mínimo; usam o SUS ou pagam direto" />
              {d.estado != null && <Kpi rotulo="Média do estado de SP" valor={pct(d.estado)} />}
              {d.posicao && <Kpi rotulo="Posição entre cidades de porte parecido" valor={`${d.posicao}ª de ${d.total_pares}`} nota="cidades de SP de 150 a 800 mil habitantes" />}
              <Kpi rotulo="Entradas e saídas no mês" valor={`${num(a.aderidos)} / ${num(a.cancelados)}`} nota="adesões / cancelamentos" />
              <Kpi rotulo="Só plano odontológico" valor={num(a.odonto)} nota="não entra na cobertura médica" />
            </div>
            <div className="painel-grade">
              <Painel titulo="Cobertura por mês" nota="% da população com plano médico-hospitalar" largo><ColunasMes a={d.cobertura_mes ?? []} rotuloA="Cobertura" formato={pct} meses={72} /></Painel>
              <Painel titulo="Beneficiários por mês" largo><ColunasMes a={d.beneficiarios_mes ?? []} rotuloA="Beneficiários" meses={72} /></Painel>
              <Painel titulo="Cobertura por faixa etária" nota="perfil de idade do Censo 2022 ajustado à população do ano"><BarrasH naOrdem max={30} pontos={d.taxa_faixa ?? []} formato={pct} rotulo="Cobertura por faixa etária" /></Painel>
              <Painel titulo="Operadoras" nota="beneficiários em plano médico"><BarrasH pontos={d.operadoras ?? []} max={12} rotulo="Beneficiários por operadora" /></Painel>
              <Painel titulo="Tipo de contrato" nota="o coletivo empresarial depende do emprego"><BarrasH pontos={d.contratos ?? []} rotulo="Beneficiários por tipo de contrato" /></Painel>
              <Painel titulo="Franca e cidades de porte parecido" nota="cobertura médico-hospitalar"><BarrasH naOrdem max={40} pontos={d.cidades ?? []} formato={pct} ativo="Franca" rotulo="Cobertura por cidade" /></Painel>
              <Painel titulo="Estimativa por bairro" nota="calculada, não medida" largo>
                {bairros.length === 0 ? <p className="vazio">A estimativa aparece depois de recalcular os cubos em <Link to="/painel/cruzamentos">Cruzamentos</Link>.</p> : (
                  <>
                    <p className="aviso-ambar">A ANS não publica bairro. Este número aplica a cobertura de Franca por faixa etária à idade dos moradores do cadastro de cada bairro. Mostra só o efeito da idade: não capta renda, e um bairro rico e um pobre com a mesma idade saem iguais.</p>
                    <div className="agenda-rolagem">
                      <table className="drill-tabela">
                        <caption className="sr-only">Cobertura estimada por bairro</caption>
                        <thead><tr><th scope="col">Bairro</th><th scope="col" aria-hidden="true" className="drill-col-barra" /><th scope="col" className="num">Cobertura estimada</th><th scope="col" className="num">Moradores no cadastro</th></tr></thead>
                        <tbody>
                          {(todos ? bairros : bairros.slice(0, VISIVEIS)).map((b) => (
                            <tr key={b.bairro}>
                              <th scope="row">{b.bairro}</th>
                              <td className="drill-col-barra" aria-hidden="true"><span className="barras-h-trilho"><span className="barras-h-barra" style={{ width: `${(b.taxa_estimada / topo) * 100}%` }} /></span></td>
                              <td className="num forte">{pct(b.taxa_estimada)}</td>
                              <td className="num">{num(b.pessoas)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {bairros.length > VISIVEIS && <div className="drill-rodape"><button type="button" className="btn secundario" onClick={() => setTodos((t) => !t)}>{todos ? `Mostrar só os ${VISIVEIS} primeiros` : `Mostrar todos (${bairros.length})`}</button></div>}
                  </>
                )}
              </Painel>
            </div>
            <p className="sutil">Fontes: ANS (Informações consolidadas de beneficiários), por município de residência informado pelas operadoras, e IBGE (estimativas de população e Censo 2022). Carregado em {dataHoraBR(d.carregado_em)}.</p>
          </>
        )}
    </section>
  )
}

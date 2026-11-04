import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, dataHoraBR } from '../api'
import { Erro, Esqueleto } from '../componentes'
import { DrillCubo, doPeriodo, useDetalhe } from './Drill'
import { BarrasH, Cabecalho, ColunasMes, Kpi, Painel, pct, reais, type Ponto, mesCurto } from './graficos'

type Totais = { empenhado: number; liquidado: number; pago: number; receita: number | null; resultado: number | null
  sem_licitacao_pct: number | null; pessoas_fisicas: number; restos_pagos: number | null }
type Filtro = { ano: number; mes_ate: number; orgao: string | null; funcao: string | null; fonte: string | null }
type Resp = {
  disponivel: { anos: number[]; ultimo_mes: Record<string, number>; publicado_tce: Record<string, string | null>
    orgaos: string[]; funcoes: string[]; fontes: string[]; carregado_em: string | null }
  filtro: Filtro | null; atual: Totais | null; anterior: Totais | null
  mensal?: { receita: Ponto[]; empenhado: Ponto[]; liquidado: Ponto[]; pago: Ponto[] }
  por_funcao?: Ponto[]; por_natureza?: Ponto[]; por_fonte?: Ponto[]; por_modalidade?: Ponto[]; por_orgao?: Ponto[]
  credores_pj?: Ponto[]; receita_origem?: Ponto[]; impostos?: Ponto[]; transferencias?: Ponto[]
}
type Escolha = { ano?: number; mes_ate?: number; orgao?: string | null; funcao?: string | null; fonte?: string | null }

const MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']
const valida = (r: unknown): r is Resp => !!r && typeof r === 'object' && Array.isArray((r as Resp).disponivel?.anos)

/** "▲ 5,2% sobre jan–ago/2025" (ou nada, se não há o ano anterior carregado). */
function variacao(atual: number | null | undefined, anterior: number | null | undefined, f: Filtro): string | undefined {
  if (atual == null || anterior == null || anterior === 0) return undefined
  const v = ((atual - anterior) / Math.abs(anterior)) * 100
  const per = f.mes_ate === 1 ? `jan/${f.ano - 1}` : `jan–${MESES[f.mes_ate - 1].slice(0, 3)}/${f.ano - 1}`
  return `${v >= 0 ? '▲' : '▼'} ${pct(Math.abs(v))} sobre ${per}`
}

function dataTce(v: string | null | undefined): string | null {
  const d = v ? new Date(v) : null
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo' }) : null
}

/** Painel Finanças (AUDESP / TCE-SP): receita, despesa por fase e por função, origem da receita e maiores credores. */
export function PainelFinancas() {
  const [esc, setEsc] = useState<Escolha>({})
  const { abrir, gaveta } = useDetalhe()
  const [d, setD] = useState<Resp | null>(null)
  const [falhou, setFalhou] = useState(false)
  const carregar = useCallback(() => {
    const q = new URLSearchParams()
    Object.entries(esc).forEach(([k, v]) => { if (v != null && v !== '') q.set(k, String(v)) })
    setFalhou(false)
    api<unknown>(`/financas/painel${q.size ? `?${q}` : ''}`).then((r) => (valida(r) ? setD(r) : setFalhou(true))).catch(() => setFalhou(true))
  }, [esc])
  useEffect(carregar, [carregar])
  const muda = (e: Escolha) => setEsc((x) => ({ ...x, ...e }))
  const alterna = (campo: 'orgao' | 'funcao' | 'fonte') => (dim: string) => muda({ [campo]: d?.filtro?.[campo] === dim ? null : dim })

  const f = d?.filtro
  const ult = f ? d?.disponivel.ultimo_mes[String(f.ano)] ?? 12 : 12
  const publicado = f ? dataTce(d?.disponivel.publicado_tce[String(f.ano)]) : null
  const filtrosDespesa = !!(f?.funcao || f?.fonte)
  const recorte = { ...(f?.orgao ? { orgao: f.orgao } : {}), ...(f?.funcao ? { funcao: f.funcao } : {}), ...(f?.fonte ? { fonte: f.fonte } : {}) }
  const despesaDoMes = (p: string) => abrir({ titulo: `Despesa liquidada em ${mesCurto(p)}`, cubo: 'despesa', fixos: { ...doPeriodo(p), fase: 'liquidado', ...recorte }, caminho: ['funcao', 'orgao', 'natureza', 'modalidade', 'fonte'] })
  return (
    <section aria-label="Finanças" className="painel painel-financas">
      <Cabecalho kicker="Gestão" titulo="Finanças" atualizado={d?.disponivel.carregado_em}
        sub="Receita e despesa que a prefeitura envia ao Tribunal de Contas (AUDESP), publicadas pelo TCE-SP." />
      {falhou ? <Erro texto="Não foi possível carregar o painel de finanças. Tente novamente em instantes." onRetry={carregar} />
        : !d ? <Esqueleto linhas={4} />
        : !f || !d.atual ? (
          <p className="vazio">Ainda não há dados do AUDESP. Carregue em <Link to="/conectores#fonte-audesp">Conectores → AUDESP (TCE-SP)</Link>.</p>
        ) : (
          <>
            <form className="filtros-fin" aria-label="Filtros do painel de finanças" onSubmit={(e) => e.preventDefault()}>
              <label>Ano
                <select value={f.ano} onChange={(e) => muda({ ano: Number(e.target.value), mes_ate: undefined })}>
                  {d.disponivel.anos.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </label>
              <label>Até o mês
                <select value={f.mes_ate} onChange={(e) => muda({ mes_ate: Number(e.target.value) })}>
                  {MESES.slice(0, ult).map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
                </select>
              </label>
              <label>Órgão
                <select value={f.orgao ?? ''} onChange={(e) => muda({ orgao: e.target.value || null })}>
                  <option value="">Todos</option>
                  {d.disponivel.orgaos.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </label>
              <label>Função
                <select value={f.funcao ?? ''} onChange={(e) => muda({ funcao: e.target.value || null })}>
                  <option value="">Todas</option>
                  {d.disponivel.funcoes.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </label>
              <label>Fonte de recurso
                <select value={f.fonte ?? ''} onChange={(e) => muda({ fonte: e.target.value || null })}>
                  <option value="">Todas</option>
                  {d.disponivel.fontes.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </label>
              {(f.orgao || f.funcao || f.fonte) && (
                <button type="button" className="btn secundario limpar" onClick={() => muda({ orgao: null, funcao: null, fonte: null })}>Limpar filtros</button>
              )}
            </form>
            <p className="sutil fin-aviso">
              Dados do TCE até {MESES[ult - 1]} de {f.ano}{publicado ? `, publicados em ${publicado}` : ''}. O TCE publica uma vez por mês, com cerca de dois meses de atraso.
              {filtrosDespesa ? ' Com filtro de função ou fonte, a receita e os restos a pagar não aparecem (não têm essa classificação).' : ''}
            </p>

            <div className="kpis" id="t-fin-kpis">
              {d.atual.receita != null && <Kpi rotulo="Receita arrecadada" valor={reais(d.atual.receita)} nota={variacao(d.atual.receita, d.anterior?.receita, f)} tom="bom" mini={d.mensal?.receita.slice(-13).map((p) => p.valor)} />}
              <Kpi rotulo="Despesa empenhada" valor={reais(d.atual.empenhado)} nota={variacao(d.atual.empenhado, d.anterior?.empenhado, f) ?? 'empenhos menos anulações'} />
              <Kpi rotulo="Despesa liquidada" valor={reais(d.atual.liquidado)} nota={variacao(d.atual.liquidado, d.anterior?.liquidado, f)} mini={d.mensal?.liquidado.slice(-13).map((p) => p.valor)} />
              <Kpi rotulo="Despesa paga" valor={reais(d.atual.pago)} nota={variacao(d.atual.pago, d.anterior?.pago, f)} />
              {d.atual.resultado != null && (
                <Kpi rotulo="Receita − despesa liquidada" valor={reais(d.atual.resultado)} tom={d.atual.resultado < 0 ? 'alerta' : 'bom'}
                  nota="resultado no período (não é o resultado orçamentário oficial)" />
              )}
              {d.atual.restos_pagos != null && <Kpi rotulo="Restos a pagar pagos" valor={reais(d.atual.restos_pagos)} nota="empenhos de anos anteriores pagos neste ano" />}
              <Kpi rotulo="Empenhado sem licitação" valor={pct(d.atual.sem_licitacao_pct)} nota="dispensa e inexigibilidade"
                tom={(d.atual.sem_licitacao_pct ?? 0) > 20 ? 'alerta' : 'normal'} />
              <Kpi rotulo="Pago a pessoas físicas" valor={reais(d.atual.pessoas_fisicas)} nota="empenhado; só o total, sem nomes" />
            </div>

            <div className="painel-grade">
              {filtrosDespesa
                ? <Painel titulo="Despesa liquidada por mês" nota="todos os anos carregados · com o filtro escolhido" largo>
                    <ColunasMes a={d.mensal?.liquidado ?? []} rotuloA="Despesa liquidada" formato={reais} meses={72} aoAbrir={despesaDoMes} />
                  </Painel>
                : <Painel titulo="Receita × despesa liquidada por mês" nota="todos os anos carregados" largo>
                    <ColunasMes a={d.mensal?.receita ?? []} b={d.mensal?.liquidado ?? []} rotuloA="Receita" rotuloB="Despesa liquidada" formato={reais} meses={72} aoAbrir={despesaDoMes} />
                  </Painel>}
              <Painel titulo="Despesa por função" nota="liquidada · clique para filtrar">
                <BarrasH pontos={d.por_funcao ?? []} formato={reais} rotulo="Despesa liquidada por função" max={12} aoEscolher={alterna('funcao')} ativo={f.funcao} />
              </Painel>
              <Painel titulo="Despesa por natureza" nota="liquidada">
                <BarrasH pontos={d.por_natureza ?? []} formato={reais} rotulo="Despesa liquidada por natureza" />
              </Painel>
              <Painel titulo="Fases da despesa" nota="empenhado → liquidado → pago">
                <BarrasH formato={reais} rotulo="Fases da despesa" pontos={[
                  { periodo: '', dim: 'Empenhado', valor: d.atual.empenhado },
                  { periodo: '', dim: 'Liquidado', valor: d.atual.liquidado },
                  { periodo: '', dim: 'Pago', valor: d.atual.pago }]} />
              </Painel>
              <Painel titulo="Despesa por órgão" nota="liquidada · clique para filtrar">
                <BarrasH pontos={d.por_orgao ?? []} formato={reais} rotulo="Despesa liquidada por órgão" aoEscolher={alterna('orgao')} ativo={f.orgao} />
              </Painel>
              <Painel titulo="Despesa por fonte de recurso" nota="liquidada · clique para filtrar">
                <BarrasH pontos={d.por_fonte ?? []} formato={reais} rotulo="Despesa liquidada por fonte de recurso" aoEscolher={alterna('fonte')} ativo={f.fonte} />
              </Painel>
              {!filtrosDespesa && (
                <>
                  <Painel titulo="Origem da receita">
                    <BarrasH pontos={d.receita_origem ?? []} formato={reais} rotulo="Receita por origem" />
                  </Painel>
                  <Painel titulo="Impostos e taxas" nota="IPTU, ISS, ITBI, IR retido…">
                    <BarrasH pontos={d.impostos ?? []} formato={reais} rotulo="Receita de impostos e taxas" />
                  </Painel>
                  <Painel titulo="Transferências recebidas" nota="FUNDEB, ICMS, IPVA, FPM, SUS…">
                    <BarrasH pontos={d.transferencias ?? []} formato={reais} rotulo="Transferências correntes recebidas" />
                  </Painel>
                </>
              )}
              <Painel titulo="Modalidade de licitação" nota="empenhado">
                <BarrasH pontos={d.por_modalidade ?? []} formato={reais} rotulo="Despesa empenhada por modalidade de licitação" />
              </Painel>
              <Painel titulo="Maiores credores (pessoa jurídica)" nota="empenhado · inclui repasses ao próprio município e ao INSS">
                <BarrasH pontos={d.credores_pj ?? []} formato={reais} rotulo="Maiores credores pessoa jurídica" max={15} />
              </Painel>
              <Painel titulo="Explorar a despesa" nota={`liquidada em ${f.ano}, até ${MESES[f.mes_ate - 1]} · clique numa linha para descer de nível`} largo>
                <DrillCubo key={JSON.stringify([f.ano, f.mes_ate, recorte])} cubo="despesa" fixos={{ ano: f.ano, fase: 'liquidado', ...recorte }} mesAte={f.mes_ate}
                  caminho={['funcao', 'orgao', 'natureza', 'modalidade', 'fonte', 'mes']} nomeArquivo={`despesa-${f.ano}`} />
              </Painel>
              {!filtrosDespesa && (
                <Painel titulo="Explorar a receita" nota={`em ${f.ano}, até ${MESES[f.mes_ate - 1]} · clique numa linha para descer de nível`} largo>
                  <DrillCubo key={JSON.stringify([f.ano, f.mes_ate, f.orgao])} cubo="receita" fixos={{ ano: f.ano, ...(f.orgao ? { orgao: f.orgao } : {}) }} mesAte={f.mes_ate}
                    caminho={['subcategoria', 'item', 'orgao', 'mes']} nomeArquivo={`receita-${f.ano}`} />
                </Painel>
              )}
            </div>
            <p className="sutil">Fonte: AUDESP / Portal da Transparência Municipal do TCE-SP. Carregado no SIGPMF em {dataHoraBR(d.disponivel.carregado_em)}.</p>
            {gaveta}
          </>
        )}
    </section>
  )
}

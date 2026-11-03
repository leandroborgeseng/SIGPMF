import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, dataHoraBR } from '../api'
import { Erro, Esqueleto } from '../componentes'
import { BarrasH, Cabecalho, ColunasMes, Kpi, Painel, num, pct, reais, type Ponto } from './graficos'

type Totais = { servidores: number; suprimido: boolean; bruto?: number; descontos?: number; liquido?: number; media?: number; mediana?: number
  afastados?: number; admissoes?: number; desligamentos?: number }
type Filtro = { ano: number; mes: number; secretaria: string | null; grupo: string | null }
type Resp = {
  disponivel: { meses: { ano: number; mes: number }[]; secretarias: string[]; carregado_em: string | null; anos_sem_13?: number[] }
  filtro: Filtro | null; atual: Totais | null; anterior?: Totais | null
  mensal?: { bruto: Ponto[]; servidores: Ponto[] }
  anual?: { bruto: Ponto[]; servidores: Ponto[]; admissoes: Ponto[]; desligamentos: Ponto[] }
  por_secretaria_bruto?: Ponto[]; por_secretaria_servidores?: Ponto[]; por_cargo_servidores?: Ponto[]; por_cargo_media?: Ponto[]
  por_situacao?: Ponto[]; por_faixa?: Ponto[]; por_tempo?: Ponto[]
}
type Escolha = { ano?: number; mes?: number; secretaria?: string | null; grupo?: string | null }

const MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro', '13º salário']
const GRUPOS: [string, string][] = [['', 'Todos'], ['ativa', 'Em atividade (secretarias)'], ['inativos', 'Aposentados e pensionistas'], ['outros', 'Cedidos e indenizados']]
const valida = (r: unknown): r is Resp => !!r && typeof r === 'object' && Array.isArray((r as Resp).disponivel?.meses)

/** "▲ 5,2% sobre set/2025" (ou nada, se o mesmo mês do ano anterior não existe ou foi suprimido). */
function variacao(atual: number | undefined, anterior: number | undefined, f: Filtro): string | undefined {
  if (atual == null || !anterior) return undefined
  const v = ((atual - anterior) / anterior) * 100
  const per = f.mes === 13 ? `13º/${f.ano - 1}` : `${MESES[f.mes - 1].slice(0, 3)}/${f.ano - 1}`
  return `${v >= 0 ? '▲' : '▼'} ${pct(Math.abs(v))} sobre ${per}`
}

/** Painel de RH (Portal da Transparência): quadro, custo da folha e evolução, por secretaria. Só números agregados. */
export function PainelRh() {
  const [esc, setEsc] = useState<Escolha>({})
  const [d, setD] = useState<Resp | null>(null)
  const [falhou, setFalhou] = useState(false)
  const carregar = useCallback(() => {
    const q = new URLSearchParams()
    Object.entries(esc).forEach(([k, v]) => { if (v != null && v !== '') q.set(k, String(v)) })
    setFalhou(false)
    api<unknown>(`/rh/painel${q.size ? `?${q}` : ''}`).then((r) => (valida(r) ? setD(r) : setFalhou(true))).catch(() => setFalhou(true))
  }, [esc])
  useEffect(carregar, [carregar])
  const muda = (e: Escolha) => setEsc((x) => ({ ...x, ...e }))
  const f = d?.filtro
  const a = d?.atual
  const anos = d ? [...new Set(d.disponivel.meses.map((m) => m.ano))].sort((x, y) => y - x) : []
  const mesesAno = d && f ? d.disponivel.meses.filter((m) => m.ano === f.ano).map((m) => m.mes) : []
  const escolheSec = (dim: string) => muda({ secretaria: f?.secretaria === dim ? null : dim })
  return (
    <section aria-label="Recursos Humanos" className="painel painel-financas">
      <Cabecalho kicker="Gestão" titulo="Recursos Humanos" atualizado={d?.disponivel.carregado_em}
        sub="Quadro de servidores e custo da folha, do Portal da Transparência. Só números somados; grupos com menos de 5 pessoas ficam ocultos." />
      {falhou ? <Erro texto="Não foi possível carregar o painel de RH. Tente novamente em instantes." onRetry={carregar} />
        : !d ? <Esqueleto linhas={4} />
        : !f || !a ? (
          <p className="vazio">Ainda não há dados de RH. Carregue em <Link to="/conectores#fonte-rh">Conectores → RH (Portal da Transparência)</Link>.</p>
        ) : (
          <>
            <form className="filtros-fin" aria-label="Filtros do painel de RH" onSubmit={(e) => e.preventDefault()}>
              <label>Ano
                <select value={f.ano} onChange={(e) => {
                  const ano = Number(e.target.value)
                  const meses = d.disponivel.meses.filter((m) => m.ano === ano && m.mes !== 13).map((m) => m.mes)
                  muda({ ano, mes: meses.includes(f.mes) ? f.mes : meses[meses.length - 1] })
                }}>{anos.map((x) => <option key={x} value={x}>{x}</option>)}</select>
              </label>
              <label>Mês
                <select value={f.mes} onChange={(e) => muda({ ano: f.ano, mes: Number(e.target.value) })}>
                  {mesesAno.map((m) => <option key={m} value={m}>{MESES[m - 1]}</option>)}
                </select>
              </label>
              <label>Secretaria
                <select value={f.secretaria ?? ''} onChange={(e) => muda({ secretaria: e.target.value || null })}>
                  <option value="">Todas</option>
                  {d.disponivel.secretarias.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
              <label>Vínculo
                <select value={f.grupo ?? ''} onChange={(e) => muda({ grupo: e.target.value || null })}>
                  {GRUPOS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                </select>
              </label>
              {(f.secretaria || f.grupo) && <button type="button" className="btn secundario limpar" onClick={() => muda({ secretaria: null, grupo: null })}>Limpar filtros</button>}
            </form>
            {a.suprimido ? <p className="aviso-ambar">Este recorte tem menos de 5 pessoas e não é mostrado, para não identificar ninguém.</p> : (
              <div className="kpis" id="t-rh-kpis">
                <Kpi rotulo="Pessoas pagas no mês" valor={num(a.servidores)} nota={variacao(a.servidores, d.anterior?.servidores, f)} />
                <Kpi rotulo="Folha bruta" valor={reais(a.bruto ?? null)} nota={variacao(a.bruto, d.anterior?.bruto, f)} />
                <Kpi rotulo="Folha líquida" valor={reais(a.liquido ?? null)} nota={`descontos de ${reais(a.descontos ?? null)}`} />
                <Kpi rotulo="Remuneração bruta média" valor={reais(a.media ?? null)} nota={variacao(a.media, d.anterior?.media, f)} />
                <Kpi rotulo="Remuneração bruta mediana" valor={reais(a.mediana ?? null)} nota="metade recebe até este valor" />
                {f.mes !== 13 && <Kpi rotulo="Admissões no mês" valor={num(a.admissoes ?? 0)} tom="bom" />}
                {f.mes !== 13 && <Kpi rotulo="Desligamentos no mês" valor={num(a.desligamentos ?? 0)} tom={(a.desligamentos ?? 0) > (a.admissoes ?? 0) ? 'alerta' : 'normal'} />}
                <Kpi rotulo="Afastados" valor={num(a.afastados ?? 0)} nota="o portal só informa a situação nos anos mais recentes" />
              </div>
            )}
            <div className="painel-grade">
              <Painel titulo="Folha bruta por mês" nota={`últimos 13 meses${f.secretaria ? ` · ${f.secretaria}` : ''} · sem 13º`} largo>
                <ColunasMes a={d.mensal?.bruto ?? []} rotuloA="Folha bruta" formato={reais} meses={13} />
              </Painel>
              <Painel titulo="Pessoas pagas por mês" nota="últimos 13 meses" largo>
                <ColunasMes a={d.mensal?.servidores ?? []} rotuloA="Pessoas pagas" meses={13} />
              </Painel>
              <Painel titulo="Evolução anual: folha bruta" nota={d.disponivel.anos_sem_13?.length ? `com 13º, exceto ${d.disponivel.anos_sem_13.join(', ')} (o portal não publicou)` : 'com 13º'} largo>
                <ColunasMes a={d.anual?.bruto ?? []} rotuloA="Folha bruta no ano" formato={reais} meses={20} />
              </Painel>
              <Painel titulo="Evolução anual: pessoas pagas" nota="média por mês no ano" largo>
                <ColunasMes a={d.anual?.servidores ?? []} rotuloA="Pessoas pagas (média mensal)" meses={20} />
              </Painel>
              <Painel titulo="Evolução anual: admissões × desligamentos" nota="o portal quase não informa demissões antes de 2016" largo>
                <ColunasMes a={d.anual?.admissoes ?? []} b={d.anual?.desligamentos ?? []} rotuloA="Admissões" rotuloB="Desligamentos" meses={20} />
              </Painel>
              <Painel titulo="Folha bruta por secretaria" nota="no mês · clique para filtrar">
                <BarrasH pontos={d.por_secretaria_bruto ?? []} formato={reais} rotulo="Folha bruta por secretaria" max={20} aoEscolher={escolheSec} ativo={f.secretaria} />
              </Painel>
              <Painel titulo="Pessoas por secretaria" nota="no mês · clique para filtrar">
                <BarrasH pontos={d.por_secretaria_servidores ?? []} rotulo="Pessoas por secretaria" max={20} aoEscolher={escolheSec} ativo={f.secretaria} />
              </Painel>
              <Painel titulo="Cargos com mais pessoas" nota="no mês">
                <BarrasH pontos={d.por_cargo_servidores ?? []} rotulo="Cargos com mais pessoas" max={15} />
              </Painel>
              <Painel titulo="Remuneração bruta média por cargo" nota="entre os cargos de maior custo">
                <BarrasH pontos={d.por_cargo_media ?? []} formato={reais} rotulo="Remuneração média por cargo" max={15} cor="alerta" />
              </Painel>
              <Painel titulo="Pessoas por faixa de remuneração" nota="bruta, no mês">
                <BarrasOrdem pontos={d.por_faixa ?? []} rotulo="Pessoas por faixa de remuneração" />
              </Painel>
              <Painel titulo="Tempo de casa" nota="pela data de admissão">
                <BarrasOrdem pontos={d.por_tempo ?? []} rotulo="Pessoas por tempo de casa" />
              </Painel>
            </div>
            <p className="sutil">Fonte: Portal da Transparência de Franca, Servidores - Remuneração. Carregado em {dataHoraBR(d.disponivel.carregado_em)}. Secretarias com nomes padronizados ao longo dos anos.</p>
          </>
        )}
    </section>
  )
}

/** Faixas em ordem própria (até 3 mil, 3 a 5 mil...): sem a ordenação por valor do BarrasH. */
function BarrasOrdem({ pontos, rotulo }: { pontos: Ponto[]; rotulo: string }) {
  if (pontos.length === 0) return <p className="sutil painel-vazio">Sem dados neste recorte.</p>
  const topo = Math.max(...pontos.map((p) => p.valor), 1)
  return (
    <ul className="barras-h" aria-label={rotulo}>
      {pontos.map((p) => (
        <li key={p.dim}>
          <span className="barras-h-rotulo" title={p.dim}>{p.dim}</span>
          <span className="barras-h-trilho" aria-hidden="true"><span className="barras-h-barra brand" style={{ width: `${Math.max(2, (p.valor / topo) * 100)}%` }} /></span>
          <span className="barras-h-valor num">{num(p.valor)}</span>
        </li>
      ))}
    </ul>
  )
}

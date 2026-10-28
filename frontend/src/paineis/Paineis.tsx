import { Link } from 'react-router-dom'
import AgendaCargas from '../AgendaCargas'
import Recursos from '../Recursos'
import { useAuth } from '../auth'
import { Erro, Esqueleto } from '../componentes'
import { BarrasH, Cabecalho, ColunasMes, Kpi, Painel, kpi, num, pct, reais, serie, useIndicadores } from './graficos'

const SERIES_GERAL = ['kpi', 'trib_lancado_mes', 'trib_pago_mes', 'saude_atend_mes']
const SERIES_SAUDE = ['kpi', 'saude_atend_mes', 'saude_atend_unidade', 'saude_espera_especialidade', 'saude_agend_especialidade',
  'saude_vacina_mes', 'saude_vacina_tipo']
const SERIES_FAZENDA = ['kpi', 'trib_lancado_mes', 'trib_pago_mes', 'trib_inadimplencia_tributo', 'trib_divida_ano',
  'trib_devedores_faixa', 'trib_saldo_faixa', 'trib_maiores_saldos']

function Estado({ falhou, carregar }: { falhou: boolean; carregar: () => void }) {
  return falhou ? <Erro texto="Não foi possível carregar os indicadores. Tente novamente em instantes." onRetry={carregar} /> : <Esqueleto linhas={4} />
}

/** Painel do gabinete: KPIs de todas as áreas, lançado × arrecadado, atendimentos e atalhos. */
export function PainelGeral() {
  const { d, falhou, carregar } = useIndicadores(SERIES_GERAL)
  return (
    <section aria-labelledby="t-geral" className="painel">
      <Cabecalho kicker="Gestão" titulo="Visão geral" sub="Indicadores consolidados das bases integradas (sem dados pessoais)." atualizado={d?.atualizado_em} />
      {!d ? <Estado falhou={falhou} carregar={carregar} /> : (
        <>
          <div className="kpis" id="t-geral">
            <Kpi rotulo="Arrecadado no exercício" valor={reais(kpi(d, 'arrecadado_ano'))} nota={`de ${reais(kpi(d, 'lancado_ano'))} lançados`} />
            <Kpi rotulo="Inadimplência" valor={pct(kpi(d, 'inadimplencia_pct'))} nota="vencido e não pago" tom={(kpi(d, 'inadimplencia_pct') ?? 0) >= 20 ? 'alerta' : 'normal'} />
            <Kpi rotulo="Dívida ativa" valor={reais(kpi(d, 'divida_ativa'))} nota={`${num(kpi(d, 'devedores'))} imóveis devedores`} tom="alerta" />
            <Kpi rotulo="Atendimentos de saúde (12 meses)" valor={num(kpi(d, 'atendimentos_12m'))} />
            <Kpi rotulo="Espera média por especialista" valor={`${num(kpi(d, 'espera_media_dias'))} dias`} tom={(kpi(d, 'espera_media_dias') ?? 0) > 60 ? 'alerta' : 'normal'} />
            <Kpi rotulo="Vacinas aplicadas (24 meses)" valor={num(kpi(d, 'vacinas_24m'))} tom="bom" />
            <Kpi rotulo="Cidadãos identificados" valor={num(kpi(d, 'cidadaos'))} />
            <Kpi rotulo="Imóveis cadastrados" valor={num(kpi(d, 'imoveis'))} />
          </div>
          <div className="painel-grade">
            <Painel titulo="Lançado × arrecadado por mês de vencimento" largo>
              <ColunasMes a={serie(d, 'trib_lancado_mes')} b={serie(d, 'trib_pago_mes')} rotuloA="Lançado" rotuloB="Arrecadado" formato={reais} />
            </Painel>
            <Painel titulo="Atendimentos de saúde por mês">
              <ColunasMes a={serie(d, 'saude_atend_mes')} rotuloA="Atendimentos" />
            </Painel>
          </div>
          <nav className="painel-atalhos" aria-label="Painéis por área">
            <Link className="btn secundario" to="/painel/saude">Painel da Saúde</Link>
            <Link className="btn secundario" to="/painel/fazenda">Painel da Fazenda</Link>
            <Link className="btn secundario" to="/monitor">Monitor de cargas</Link>
          </nav>
        </>
      )}
    </section>
  )
}

export function PainelSaude() {
  const { d, falhou, carregar } = useIndicadores(SERIES_SAUDE)
  return (
    <section aria-labelledby="t-saude" className="painel">
      <Cabecalho kicker="Gestão" titulo="Saúde" sub="Atendimentos, filas e vacinação — sem conteúdo clínico; grupos com menos de 5 casos ficam ocultos." atualizado={d?.atualizado_em} />
      {!d ? <Estado falhou={falhou} carregar={carregar} /> : (
        <>
          <div className="kpis" id="t-saude">
            <Kpi rotulo="Atendimentos (12 meses)" valor={num(kpi(d, 'atendimentos_12m'))} />
            <Kpi rotulo="Espera média por especialista" valor={`${num(kpi(d, 'espera_media_dias'))} dias`} tom={(kpi(d, 'espera_media_dias') ?? 0) > 60 ? 'alerta' : 'normal'} />
            <Kpi rotulo="Vacinas aplicadas (24 meses)" valor={num(kpi(d, 'vacinas_24m'))} tom="bom" />
          </div>
          <div className="painel-grade">
            <Painel titulo="Atendimentos por mês" largo><ColunasMes a={serie(d, 'saude_atend_mes')} rotuloA="Atendimentos" /></Painel>
            <Painel titulo="Atendimentos por unidade (12 meses)"><BarrasH pontos={serie(d, 'saude_atend_unidade')} rotulo="Atendimentos por unidade" /></Painel>
            <Painel titulo="Espera média por especialidade" nota="dias entre a marcação e a consulta">
              <BarrasH pontos={serie(d, 'saude_espera_especialidade')} formato={(v) => `${num(v)} d`} cor="alerta" rotulo="Espera por especialidade" />
            </Painel>
            <Painel titulo="Agendamentos por especialidade"><BarrasH pontos={serie(d, 'saude_agend_especialidade')} rotulo="Agendamentos por especialidade" /></Painel>
            <Painel titulo="Doses aplicadas por vacina"><BarrasH pontos={serie(d, 'saude_vacina_tipo')} cor="ok" rotulo="Doses por vacina" /></Painel>
            <Painel titulo="Vacinação por mês" largo><ColunasMes a={serie(d, 'saude_vacina_mes')} rotuloA="Doses" meses={24} /></Painel>
          </div>
        </>
      )}
    </section>
  )
}

export function PainelFazenda() {
  const { d, falhou, carregar } = useIndicadores(SERIES_FAZENDA)
  const top = serie(d, 'trib_maiores_saldos')
  const totalDivida = kpi(d, 'divida_ativa') ?? 0
  const top10 = [...top].sort((a, b) => b.valor - a.valor).slice(0, 10).reduce((s, p) => s + p.valor, 0)
  return (
    <section aria-labelledby="t-fazenda" className="painel">
      <Cabecalho kicker="Gestão" titulo="Fazenda" sub="Arrecadação, inadimplência e dívida ativa. Devedores por imóvel, sem identificação." atualizado={d?.atualizado_em} />
      {!d ? <Estado falhou={falhou} carregar={carregar} /> : (
        <>
          <div className="kpis" id="t-fazenda">
            <Kpi rotulo="Lançado no exercício" valor={reais(kpi(d, 'lancado_ano'))} />
            <Kpi rotulo="Arrecadado" valor={reais(kpi(d, 'arrecadado_ano'))} tom="bom" />
            <Kpi rotulo="Inadimplência" valor={pct(kpi(d, 'inadimplencia_pct'))} tom={(kpi(d, 'inadimplencia_pct') ?? 0) >= 20 ? 'alerta' : 'normal'} />
            <Kpi rotulo="Estoque da dívida ativa" valor={reais(totalDivida)} tom="alerta" />
            <Kpi rotulo="Imóveis devedores" valor={num(kpi(d, 'devedores'))} />
            <Kpi rotulo="Inscrições protestadas" valor={num(kpi(d, 'protestadas'))} />
          </div>
          <div className="painel-grade">
            <Painel titulo="Lançado × arrecadado por mês de vencimento" largo>
              <ColunasMes a={serie(d, 'trib_lancado_mes')} b={serie(d, 'trib_pago_mes')} rotuloA="Lançado" rotuloB="Arrecadado" formato={reais} />
            </Painel>
            <Painel titulo="Inadimplência por tributo"><BarrasH pontos={serie(d, 'trib_inadimplencia_tributo')} formato={pct} cor="alerta" rotulo="Inadimplência por tributo" /></Painel>
            <Painel titulo="Estoque da dívida por ano de origem"><BarrasH pontos={serie(d, 'trib_divida_ano')} formato={reais} max={12} rotulo="Dívida por ano" /></Painel>
            <Painel titulo="Devedores por faixa de saldo"><BarrasH pontos={serie(d, 'trib_devedores_faixa')} rotulo="Devedores por faixa" /></Painel>
            <Painel titulo="Onde está o dinheiro" nota="maiores saldos por imóvel">
              {top.length === 0 ? <p className="sutil painel-vazio">Sem dívida ativa carregada.</p> : (
                <>
                  <p className="destaque">Os 10 maiores saldos somam <strong>{reais(top10)}</strong>{totalDivida ? `, ${pct((top10 / totalDivida) * 100)} do estoque` : ''}.</p>
                  <BarrasH pontos={top} formato={reais} max={10} cor="alerta" rotulo="Maiores saldos" />
                </>
              )}
            </Painel>
          </div>
        </>
      )}
    </section>
  )
}

/** Monitor de cargas: agenda + linha do tempo, recursos do servidor (admin) e atalho para os conectores. */
export function MonitorCargas() {
  const { sessao } = useAuth()
  const ehAdmin = sessao?.perfil === 'admin'
  return (
    <section aria-labelledby="t-monitor" className="painel">
      <Cabecalho kicker="Dados" titulo="Monitor de cargas" sub="Agenda, execuções das últimas 24 horas e recursos do servidor." />
      <h2 id="t-monitor" className="sr-only">Monitor</h2>
      <AgendaCargas ehAdmin={ehAdmin} />
      {ehAdmin && <Recursos />}
      <p><Link to="/conectores">Ver conectores, amostras e cargas completas</Link></p>
    </section>
  )
}

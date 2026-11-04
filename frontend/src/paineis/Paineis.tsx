import { Link } from 'react-router-dom'
import AgendaCargas from '../AgendaCargas'
import Recursos from '../Recursos'
import { useAuth } from '../auth'
import { Erro, Esqueleto } from '../componentes'
import { DrillCubo, doPeriodo, useDetalhe, type Detalhe } from './Drill'
import { BarrasH, Cabecalho, ColunasMes, Kpi, Painel, kpi, mesCurto, num, pct, reais, serie, useIndicadores } from './graficos'

const SERIES_GERAL = ['kpi', 'trib_lancado_mes', 'trib_pago_mes', 'saude_atend_mes']
const SERIES_SAUDE = ['kpi', 'saude_atend_mes', 'saude_atend_unidade', 'saude_espera_especialidade', 'saude_agend_especialidade',
  'saude_vacina_mes', 'saude_vacina_tipo', 'saude_especialidade_sem_nome']
const SERIES_FAZENDA = ['kpi', 'trib_lancado_mes', 'trib_pago_mes', 'trib_inadimplencia_tributo', 'trib_divida_ano',
  'trib_devedores_faixa', 'trib_saldo_faixa', 'trib_maiores_saldos']

const CONSULTA = { cubo: 'saude', medidas: ['eventos'], ocultar: ['vacina', 'tipo'] }
const AGENDAMENTO = { cubo: 'saude', ocultar: ['vacina', 'tipo'] }
const VACINA = { cubo: 'saude', medidas: ['eventos'], ocultar: ['especialidade', 'tipo'] }
const SO_HISTORICO = 'Todo o histórico carregado'
const consultasDoMes = (p: string): Detalhe => ({ ...CONSULTA, titulo: `Atendimentos em ${mesCurto(p)}`, fixos: { tipo: 'consulta', ...doPeriodo(p) }, caminho: ['especialidade', 'unidade', 'bairro', 'faixa'] })
const tributosDoMes = (p: string): Detalhe => ({ titulo: `Vencimentos em ${mesCurto(p)}`, cubo: 'tributario', fixos: doPeriodo(p, 'ano_venc'), caminho: ['tributo', 'bairro', 'exercicio'], medidas: ['lancado', 'pago', 'vencido_aberto'] })

function Estado({ falhou, carregar }: { falhou: boolean; carregar: () => void }) {
  return falhou ? <Erro texto="Não foi possível carregar os indicadores. Tente novamente em instantes." onRetry={carregar} /> : <Esqueleto linhas={4} />
}

/** Painel do gabinete: KPIs de todas as áreas, lançado × arrecadado, atendimentos e atalhos. */
export function PainelGeral() {
  const { d, falhou, carregar } = useIndicadores(SERIES_GERAL)
  const { abrir, gaveta } = useDetalhe()
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
              <ColunasMes a={serie(d, 'trib_lancado_mes')} b={serie(d, 'trib_pago_mes')} rotuloA="Lançado" rotuloB="Arrecadado" formato={reais} aoAbrir={(p) => abrir(tributosDoMes(p))} />
            </Painel>
            <Painel titulo="Atendimentos de saúde por mês" nota="clique num mês para detalhar">
              <ColunasMes a={serie(d, 'saude_atend_mes')} rotuloA="Atendimentos" aoAbrir={(p) => abrir(consultasDoMes(p))} />
            </Painel>
          </div>
          <nav className="painel-atalhos" aria-label="Painéis por área">
            <Link className="btn secundario" to="/painel/saude">Painel da Saúde</Link>
            <Link className="btn secundario" to="/painel/fazenda">Painel da Fazenda</Link>
            <Link className="btn secundario" to="/monitor">Monitor de cargas</Link>
          </nav>
          {gaveta}
        </>
      )}
    </section>
  )
}

export function PainelSaude() {
  const { d, falhou, carregar } = useIndicadores(SERIES_SAUDE)
  const { abrir, gaveta } = useDetalhe()
  // ES2: parte dos códigos de especialidade não tem nome no cadastro da Saúde (Aux_Especialidades); o painel avisa
  const semNome = serie(d, 'saude_especialidade_sem_nome').find((p) => p.dim === 'agendamentos')?.valor ?? 0
  const avisoEsp = semNome > 0 ? `${pct(semNome)} dos agendamentos com especialidade sem nome no cadastro da Saúde` : undefined
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
            <Painel titulo="Atendimentos por mês" nota="clique num mês para detalhar" largo><ColunasMes a={serie(d, 'saude_atend_mes')} rotuloA="Atendimentos" aoAbrir={(p) => abrir(consultasDoMes(p))} /></Painel>
            <Painel titulo="Atendimentos por unidade (12 meses)"><BarrasH pontos={serie(d, 'saude_atend_unidade')} rotulo="Atendimentos por unidade"
              aoAbrir={(u) => abrir({ ...CONSULTA, titulo: u, sub: `Atendimentos. ${SO_HISTORICO}.`, fixos: { tipo: 'consulta', unidade: u }, caminho: ['ano', 'mes', 'especialidade', 'bairro', 'faixa'] })} /></Painel>
            <Painel titulo="Espera média por especialidade" nota={`dias entre a marcação e a consulta${avisoEsp ? ` · ${avisoEsp}` : ''}`}>
              <BarrasH pontos={serie(d, 'saude_espera_especialidade')} formato={(v) => `${num(v)} d`} rotulo="Espera por especialidade"
                aoAbrir={(e) => abrir({ ...AGENDAMENTO, titulo: e, sub: `Espera por consulta. ${SO_HISTORICO}.`, fixos: { tipo: 'agendamento', especialidade: e }, caminho: ['unidade', 'bairro', 'ano', 'faixa'], medidas: ['espera_media', 'eventos'] })} />
            </Painel>
            <Painel titulo="Agendamentos por especialidade" nota={avisoEsp}><BarrasH pontos={serie(d, 'saude_agend_especialidade')} rotulo="Agendamentos por especialidade"
              aoAbrir={(e) => abrir({ ...AGENDAMENTO, titulo: e, sub: `Agendamentos. ${SO_HISTORICO}.`, fixos: { tipo: 'agendamento', especialidade: e }, caminho: ['unidade', 'bairro', 'ano', 'faixa'], medidas: ['eventos', 'espera_media'] })} /></Painel>
            <Painel titulo="Doses aplicadas por vacina"><BarrasH pontos={serie(d, 'saude_vacina_tipo')} rotulo="Doses por vacina"
              aoAbrir={(v) => abrir({ ...VACINA, titulo: v, sub: `Doses aplicadas. ${SO_HISTORICO}.`, fixos: { tipo: 'vacina', vacina: v }, caminho: ['ano', 'mes', 'unidade', 'bairro', 'faixa'] })} /></Painel>
            <Painel titulo="Vacinação por mês" largo><ColunasMes a={serie(d, 'saude_vacina_mes')} rotuloA="Doses" meses={24}
              aoAbrir={(p) => abrir({ ...VACINA, titulo: `Doses em ${mesCurto(p)}`, fixos: { tipo: 'vacina', ...doPeriodo(p) }, caminho: ['vacina', 'unidade', 'bairro', 'faixa'] })} /></Painel>
            <Painel titulo="Explorar os atendimentos" nota="clique numa linha para descer de nível" largo>
              <DrillCubo {...CONSULTA} fixos={{ tipo: 'consulta' }} caminho={['ano', 'mes', 'especialidade', 'unidade', 'bairro', 'faixa']} nomeArquivo="atendimentos" />
            </Painel>
          </div>
          {gaveta}
        </>
      )}
    </section>
  )
}

export function PainelFazenda() {
  const { d, falhou, carregar } = useIndicadores(SERIES_FAZENDA)
  const { abrir, gaveta } = useDetalhe()
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
              <ColunasMes a={serie(d, 'trib_lancado_mes')} b={serie(d, 'trib_pago_mes')} rotuloA="Lançado" rotuloB="Arrecadado" formato={reais} aoAbrir={(p) => abrir(tributosDoMes(p))} />
            </Painel>
            <Painel titulo="Inadimplência por tributo"><BarrasH pontos={serie(d, 'trib_inadimplencia_tributo')} formato={pct} rotulo="Inadimplência por tributo"
              aoAbrir={(t) => abrir({ titulo: t, sub: `Inadimplência. ${SO_HISTORICO}.`, cubo: 'tributario', fixos: { tributo: t }, caminho: ['exercicio', 'bairro'], medidas: ['inadimplencia_pct', 'vencido_aberto', 'lancado', 'pago'] })} /></Painel>
            <Painel titulo="Estoque da dívida por ano de origem"><BarrasH pontos={serie(d, 'trib_divida_ano')} formato={reais} max={12} rotulo="Dívida por ano"
              aoAbrir={(a) => abrir({ titulo: `Dívida ativa com origem em ${a}`, cubo: 'divida', fixos: /^\d+$/.test(a) ? { ano: a } : {}, caminho: ['natureza', 'faixa_saldo', 'bairro'], medidas: ['saldo', 'inscricoes'] })} /></Painel>
            <Painel titulo="Devedores por faixa de saldo"><BarrasH pontos={serie(d, 'trib_devedores_faixa')} rotulo="Devedores por faixa" /></Painel>
            <Painel titulo="Onde está o dinheiro" nota="maiores saldos por imóvel">
              {top.length === 0 ? <p className="sutil painel-vazio">Sem dívida ativa carregada.</p> : (
                <>
                  <p className="destaque">Os 10 maiores saldos somam <strong>{reais(top10)}</strong>{totalDivida ? `, ${pct((top10 / totalDivida) * 100)} do estoque` : ''}.</p>
                  <BarrasH pontos={top} formato={reais} max={10} rotulo="Maiores saldos" />
                </>
              )}
            </Painel>
            <Painel titulo="Explorar a arrecadação" nota="clique numa linha para descer de nível" largo>
              <DrillCubo cubo="tributario" caminho={['exercicio', 'tributo', 'bairro']} medidas={['lancado', 'pago', 'vencido_aberto', 'inadimplencia_pct']} nomeArquivo="arrecadacao" />
            </Painel>
            <Painel titulo="Explorar a dívida ativa" nota="clique numa linha para descer de nível" largo>
              <DrillCubo cubo="divida" caminho={['natureza', 'ano', 'faixa_saldo', 'bairro']} medidas={['saldo', 'inscricoes']} nomeArquivo="divida-ativa" />
            </Painel>
          </div>
          {gaveta}
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

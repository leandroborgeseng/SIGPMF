import { useCallback, useEffect, useState } from 'react'
import { Area, estadoArea, ListaCurta, type EstadoArea, type Fontes, type Leitura } from './AreaFicha'
import { api, ApiErro, dataBR, dataHoraBR } from './api'
import { useAuth } from './auth'
import ImpostosCidadao, { reais, validaTributario, type RespTributario } from './ImpostosCidadao'

type Proximo = { data: string | null; unidade: string | null; especialidade: string | null; dias_espera: number | null }
type AtendS = { data: string | null; unidade: string | null; especialidade: string | null }
type VacS = { data: string | null; vacina: string | null; dose: string | null; unidade: string | null }
export type RespSaude = { cidadao_id: string; totais?: { atendimentos: number; vacinas: number }; proximos: Proximo[]; atendimentos: AtendS[]; vacinas: VacS[] }
type MatEdu = { escola: string | null; unidade: string | null; turma: string | null; inativa: boolean; data_alteracao: string | null }
type Aluno = { menor: boolean; mascarado: boolean; inativo: boolean; matricula_atual: MatEdu | null; historico: MatEdu[]
  responsaveis: { tipo: string | null; nome: string | null; principal: boolean | null }[] }
export type RespEducacao = { cidadao_id: string; alunos: Aluno[] }
type Lugar = { cemiterio: string | null; quadra: string | null; sepultura: string | null }
export type RespCemiterio = { cidadao_id: string; titular: (Lugar & { ativo: boolean | null })[]; autorizado: (Lugar & { parentesco: string | null })[]
  leilao: (Lugar & { data_venda: string | null; data_liberacao: string | null })[]
  sepultamento: (Lugar & { gaveta: string | null; lado: string | null; data_morte: string | null; data_sepultamento: string | null })[] }
type EventoT = { data: string; area: string; tipo: string; titulo: string; fonte: string }
type Acesso = { usuario: string | null; acao: string; o_que: string; quando: string }

const validaSaude = (r: unknown): r is RespSaude => !!r && Array.isArray((r as RespSaude).proximos)
  && Array.isArray((r as RespSaude).atendimentos) && Array.isArray((r as RespSaude).vacinas)
const AREA: Record<string, string> = { saude: 'Saúde', impostos: 'Impostos', familia: 'Família', pedidos: 'Pedidos' }

const validaEducacao = (r: unknown): r is RespEducacao => !!r && Array.isArray((r as RespEducacao).alunos)
const validaCemiterio = (r: unknown): r is RespCemiterio => !!r && Array.isArray((r as RespCemiterio).titular) && Array.isArray((r as RespCemiterio).sepultamento)
const validaFontes = (r: unknown): r is Fontes => !!r && typeof (r as Fontes).carregadas === 'object' && Array.isArray((r as Fontes).nao_integradas)

/** FC1: busca cada área UMA vez (cada consulta é auditada) e entrega aos blocos da ficha. Recusa (403) é diferente de
 * falha de leitura e de "não tem": a ficha nunca diz que o cidadão não tem o que o usuário só não pode ver. */
export function useDadosFicha(id: string) {
  const [trib, setTrib] = useState<RespTributario | null | false>(null)
  const [saude, setSaude] = useState<RespSaude | null | false>(null)
  const [educacao, setEducacao] = useState<RespEducacao | null | false>(null)
  const [cemiterio, setCemiterio] = useState<RespCemiterio | null | false>(null)
  const [fontes, setFontes] = useState<Fontes | null | false>(null)
  const [recusas, setRecusas] = useState<string[]>([])
  useEffect(() => {
    setTrib(null); setSaude(null); setEducacao(null); setCemiterio(null); setFontes(null); setRecusas([])
    const c = encodeURIComponent(id)
    const busca = <T,>(area: string, valida: (r: unknown) => r is T, grava: (v: T | false) => void) =>
      api<unknown>(`/gold/cidadaos/${c}/${area}`).then((r) => grava(valida(r) ? r : false)).catch((e) => {
        if (e instanceof ApiErro && e.status === 403) setRecusas((v) => [...v, area])
        grava(false)
      })
    busca('tributario', validaTributario, setTrib)
    busca('saude', validaSaude, setSaude)
    busca('educacao', validaEducacao, setEducacao)
    busca('cemiterio', validaCemiterio, setCemiterio)
    api<unknown>('/gold/ficha/fontes').then((r) => setFontes(validaFontes(r) ? r : false)).catch(() => setFontes(false))
  }, [id])
  const leitura = (area: string, v: unknown): Leitura => (recusas.includes(area) ? 'sem_acesso' : v === null ? 'carregando' : v === false ? 'falha' : 'ok')
  return { trib, saude, educacao, cemiterio, fontes, leituras: { tributario: leitura('tributario', trib), saude: leitura('saude', saude),
    educacao: leitura('educacao', educacao), cemiterio: leitura('cemiterio', cemiterio) } }
}

const ROTULO_ESTADO: Record<EstadoArea, string> = { carregando: 'Carregando', tem: '', nao_tem: 'Não tem', sem_base: 'Base não carregada', sem_acesso: 'Sem acesso', falha: 'Erro de leitura' }
export type ItemMapa = { id: string; nome: string; estado: EstadoArea; valor?: string }

/** Mapa da ficha: todas as áreas de uma vez, cada uma leva ao seu bloco. O estado está em texto, nunca só na cor. */
export function MapaAreas({ itens, naoIntegradas }: { itens: ItemMapa[]; naoIntegradas: string[] }) {
  const ir = (id: string) => {
    const el = document.getElementById(`area-${id}`)
    el?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }); el?.focus({ preventScroll: true })
  }
  return (
    <section className="mapa-areas" aria-labelledby="t-mapa">
      <h3 id="t-mapa">O que existe para este cidadão</h3>
      <ul>
        {itens.map((i) => (
          <li key={i.id}>
            <button type="button" className={`mapa-area est-${i.estado}`} onClick={() => ir(i.id)}>
              <span className="mapa-nome">{i.nome}</span>
              <span className="mapa-valor">{i.estado === 'tem' ? i.valor : ROTULO_ESTADO[i.estado]}</span>
            </button>
          </li>
        ))}
      </ul>
      {naoIntegradas.length > 0 && <p className="sutil mapa-nota">Ainda não integradas ao SIGPMF, sem como consultar: {naoIntegradas.join(', ')}.</p>}
    </section>
  )
}

type Pendencia = { area: string; titulo: string; detalhe: string; tom: 'alerta' | 'info' | 'ok' }

export function pendencias(trib: RespTributario | null | false, saude: RespSaude | null | false): Pendencia[] {
  const out: Pendencia[] = []
  if (trib) {
    const t = trib.totais
    if (t.parcelas_vencidas > 0) out.push({ area: 'Impostos', titulo: `${t.parcelas_vencidas} parcela(s) vencida(s)`, detalhe: `${reais(t.vencido)} em atraso`, tom: 'alerta' })
    if (t.divida_ativa > 0) out.push({ area: 'Impostos', titulo: 'Débito em dívida ativa', detalhe: `${reais(t.divida_ativa)}${t.protestada ? ' · protestado' : ''}`, tom: 'alerta' })
    const aVencer = trib.lancamentos.filter((x) => x.situacao === 'a_vencer').sort((a, b) => String(a.vencimento).localeCompare(String(b.vencimento)))[0]
    if (aVencer) out.push({ area: 'Impostos', titulo: `Próxima parcela vence em ${dataBR(aVencer.vencimento)}`, detalhe: reais(aVencer.valor), tom: 'info' })
    const comDebito = (trib.empresas ?? []).filter((e) => e.possui_debito)
    if (comDebito.length) out.push({ area: 'Empresas', titulo: comDebito.length === 1 ? 'Empresa com débito na prefeitura' : `${comDebito.length} empresas com débito na prefeitura`, detalhe: comDebito.map((e) => `CCM ${e.ccm}`).join(', '), tom: 'alerta' })
  }
  if (saude && saude.proximos[0]) {
    const p = saude.proximos[0]
    out.push({ area: 'Saúde', titulo: `Consulta ${p.especialidade && !p.especialidade.startsWith('Sem especialidade') ? `de ${p.especialidade} ` : ''}em ${dataBR(p.data)}`, detalhe: p.unidade ?? 'Unidade de saúde', tom: 'info' })
  }
  return out
}

/** FC1: "Para fazer agora" a partir das áreas que o usuário pode ver. */
export function ResumoPortal({ trib, saude }: { trib: RespTributario | null | false; saude: RespSaude | null | false }) {
  if (trib === null || saude === null) return <section className="cartao portal-resumo"><p className="sutil">Carregando resumo…</p></section>
  const lista = pendencias(trib, saude)
  return (
    <section className="portal-resumo" aria-label="Resumo do cidadão">
      <div className="cartao portal-pendencias">
        <h3>Para fazer agora</h3>
        {lista.length === 0 ? <p className="sutil">Nenhuma pendência nas áreas que você pode ver.</p> : (
          <ul aria-label="Pendências">
            {lista.map((p) => (
              <li key={`${p.area}-${p.titulo}`} className={`pend pend-${p.tom}`}>
                <span className="pend-area">{p.area}</span>
                <strong>{p.titulo}</strong>
                <span className="sutil">{p.detalhe}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

/** FC2: relacionamento com a Saúde, sem conteúdo clínico. Vacinas, consultas e agendamentos aparecem sempre. */
export function SaudeCidadao({ saude, leitura, fontes }: { saude: RespSaude | null | false; leitura?: Leitura; fontes?: Fontes | null | false }) {
  const l: Leitura = leitura ?? (saude === null ? 'carregando' : saude === false ? 'falha' : 'ok')
  const d = saude || null
  const vac = d?.totais?.vacinas ?? d?.vacinas.length ?? 0
  const ate = d?.totais?.atendimentos ?? d?.atendimentos.length ?? 0
  return (
    <section className="cartao portal-bloco" aria-labelledby="t-saude-cid">
      <h3 id="t-saude-cid">Saúde</h3>
      <Area id="vacinas" titulo="Vacinas" estado={estadoArea(l, vac, fontes, 'vacinas')} total={vac} vazio="Nenhuma vacina registrada para este cidadão.">
        {d && <ListaCurta itens={d.vacinas} rotulo="Vacinas" render={(v, i) => (
          <li key={`${v.vacina}-${v.data}-${i}`}><span className="dia num">{dataBR(v.data)}</span><span><strong>{v.vacina}{v.dose ? ` ${v.dose}` : ''}</strong><span className="sutil"> · {v.unidade ?? 'Unidade não informada'}</span></span></li>
        )} />}
      </Area>
      <Area id="consultas" titulo="Consultas realizadas" estado={estadoArea(l, ate, fontes, 'consultas')} total={ate} vazio="Nenhuma consulta registrada para este cidadão.">
        {d && <ListaCurta itens={d.atendimentos} rotulo="Consultas realizadas" render={(a, i) => (
          <li key={`${a.data}-${i}`}><span className="dia num">{dataBR(a.data)}</span><span>{a.especialidade ?? 'Atendimento'}<span className="sutil"> · {a.unidade ?? 'Unidade não informada'}</span></span></li>
        )} />}
      </Area>
      <Area id="agendamentos" titulo="Consultas agendadas" estado={estadoArea(l, d?.proximos.length ?? 0, fontes, 'agendamentos')} total={d?.proximos.length} vazio="Nenhuma consulta agendada a partir de hoje.">
        {d && <ListaCurta itens={d.proximos} rotulo="Consultas agendadas" render={(p) => (
          <li key={`${p.data}-${p.unidade}-${p.especialidade}`}>
            <span className="dia num">{dataBR(p.data)}</span>
            <span><strong>{p.especialidade ?? 'Consulta'}</strong><span className="sutil"> · {p.unidade ?? 'Unidade não informada'}{p.dias_espera != null ? ` · ${p.dias_espera} dias de espera` : ''}</span></span>
          </li>
        )} />}
      </Area>
      <p className="nota">Diagnósticos e anotações clínicas não aparecem aqui.</p>
    </section>
  )
}

const nomeMat = (m: MatEdu) => [m.escola || m.unidade || 'Escola não informada', m.turma ? `turma ${m.turma}` : null].filter(Boolean).join(' · ')

/** GE9: matrícula atual e histórico. Dado de aluno menor chega mascarado do backend (GE4). */
export function EducacaoCidadao({ educacao, leitura, fontes }: { educacao: RespEducacao | null | false; leitura: Leitura; fontes?: Fontes | null | false }) {
  const alunos = educacao ? educacao.alunos : []
  return (
    <section className="cartao portal-bloco" aria-labelledby="t-edu-cid">
      <h3 id="t-edu-cid">Educação</h3>
      <Area id="educacao" titulo="Matrículas na rede municipal" estado={estadoArea(leitura, alunos.length, fontes, 'educacao')} vazio="Nenhuma matrícula na rede municipal para este cidadão.">
        {alunos.map((a, i) => (
          <div key={i} className="edu-aluno">
            {a.matricula_atual ? (
              <p><span className="selo sucesso"><i aria-hidden="true" />Matrícula atual</span> <strong>{nomeMat(a.matricula_atual)}</strong></p>
            ) : <p className="sutil">Sem matrícula ativa{a.inativo ? ' (cadastro inativo)' : ''}.</p>}
            {a.historico.filter((m) => m.inativa).length > 0 && (
              <ListaCurta itens={a.historico.filter((m) => m.inativa)} passo={3} rotulo="Matrículas anteriores" render={(m, j) => (
                <li key={j}><span className="dia num">{m.data_alteracao ? dataBR(m.data_alteracao) : 'Sem data'}</span><span>{nomeMat(m)}<span className="sutil"> · anterior</span></span></li>
              )} />
            )}
            {a.responsaveis.length > 0 && <p className="sutil">Responsáveis: {a.responsaveis.map((r) => r.nome || 'Não informado').join(', ')}</p>}
            {a.mascarado && <p className="nota">Aluno menor de idade: dados pessoais mascarados.</p>}
          </div>
        ))}
      </Area>
    </section>
  )
}

const onde = (l: Lugar) => [l.cemiterio, l.quadra ? `quadra ${l.quadra}` : null, l.sepultura ? `sepultura ${l.sepultura}` : null].filter(Boolean).join(' · ') || 'Local não informado'

/** Túmulos ligados ao CPF do cidadão e sepultamento (só com óbito confirmado por pessoa). */
export function CemiterioCidadao({ cemiterio, leitura, fontes }: { cemiterio: RespCemiterio | null | false; leitura: Leitura; fontes?: Fontes | null | false }) {
  const c = cemiterio || null
  const tumulos = (c?.titular.length ?? 0) + (c?.autorizado.length ?? 0) + (c?.leilao.length ?? 0)
  return (
    <section className="cartao portal-bloco" aria-labelledby="t-cem-cid">
      <h3 id="t-cem-cid">Cemitério</h3>
      <Area id="tumulos" titulo="Túmulos" estado={estadoArea(leitura, tumulos, fontes, 'cemiterio')} total={tumulos} vazio="Nenhum túmulo ligado ao CPF deste cidadão.">
        {c && (
          <ul className="impostos-imoveis" aria-label="Túmulos">
            {c.titular.map((t, i) => <li key={`t${i}`} className={t.ativo === false ? 'inativo' : ''}><span><strong>{onde(t)}</strong></span><span className={`selo ${t.ativo === false ? 'neutro' : 'sucesso'}`}><i aria-hidden="true" />{t.ativo === false ? 'Foi titular' : 'Titular'}</span></li>)}
            {c.autorizado.map((t, i) => <li key={`a${i}`}><span><strong>{onde(t)}</strong>{t.parentesco ? <span className="sutil"> · {t.parentesco}</span> : null}</span><span className="selo neutro"><i aria-hidden="true" />Autorizado</span></li>)}
            {c.leilao.map((t, i) => <li key={`l${i}`}><span><strong>{onde(t)}</strong>{t.data_venda ? <span className="sutil"> · em {dataBR(t.data_venda)}</span> : null}</span><span className="selo neutro"><i aria-hidden="true" />Comprado em leilão</span></li>)}
          </ul>
        )}
      </Area>
      <Area id="sepultamento" titulo="Sepultamento" estado={estadoArea(leitura, c?.sepultamento.length ?? 0, fontes, 'cemiterio')} vazio="Nenhum sepultamento confirmado para este cidadão.">
        {c && (
          <ul className="impostos-imoveis" aria-label="Sepultamento">
            {c.sepultamento.map((s, i) => <li key={i}><span><strong>{onde(s)}</strong><span className="sutil">{s.gaveta ? ` · gaveta ${s.gaveta}` : ''}{s.lado ? ` · lado ${s.lado}` : ''}{s.data_sepultamento ? ` · sepultado em ${dataBR(s.data_sepultamento)}` : ''}</span></span></li>)}
          </ul>
        )}
      </Area>
    </section>
  )
}

/** FC3: linha do tempo de todas as áreas, 20 por vez (carregar mais). */
export function LinhaDoTempoCidadao({ id }: { id: string }) {
  const [evs, setEvs] = useState<EventoT[] | null>(null)
  const [falhou, setFalhou] = useState(false)
  const [fim, setFim] = useState(false)
  const pagina = useCallback(async (antes?: string) => {
    try {
      const q = `/gold/cidadaos/${encodeURIComponent(id)}/linha-do-tempo?limite=20${antes ? `&antes=${antes}` : ''}`
      const r = await api<{ eventos?: EventoT[] }>(q)
      const novos = Array.isArray(r?.eventos) ? r.eventos : []
      setEvs((v) => (antes ? [...(v ?? []), ...novos] : novos))
      setFim(novos.length < 20)
    } catch {
      setFalhou(true)
    }
  }, [id])
  useEffect(() => { setEvs(null); setFalhou(false); setFim(false); pagina() }, [pagina])
  return (
    <section className="cartao portal-bloco" aria-labelledby="t-linha">
      <h3 id="t-linha">Linha do tempo</h3>
      {falhou ? <p className="sutil">Sem leitura da linha do tempo.</p> : evs === null ? <p className="sutil">Carregando…</p>
        : evs.length === 0 ? <p className="sutil">Nenhum evento registrado.</p> : (
          <>
            <ol className="linha-tempo">
              {evs.map((e, i) => (
                <li key={`${e.data}-${e.titulo}-${i}`} className={`area-${e.area}`}>
                  <span className="lt-data num">{dataBR(e.data)}</span>
                  <span className="lt-ponto" aria-hidden="true" />
                  <span><strong>{e.titulo}</strong><span className="sutil"> · {AREA[e.area] ?? e.area} · {e.fonte}</span></span>
                </li>
              ))}
            </ol>
            {!fim && <button className="btn secundario" onClick={() => pagina(evs[evs.length - 1].data)}>Carregar mais</button>}
          </>
        )}
    </section>
  )
}

/** FC4: quem consultou a ficha (só admin e admin_lgpd; a lista também é auditada). */
export function AcessosCidadao({ id }: { id: string }) {
  const { sessao } = useAuth()
  const pode = sessao?.perfil === 'admin' || sessao?.perfil === 'admin_lgpd'
  const [a, setA] = useState<Acesso[] | null>(null)
  const [falhou, setFalhou] = useState(false)
  const ver = () => {
    setFalhou(false)
    api<{ acessos?: Acesso[] }>(`/gold/cidadaos/${encodeURIComponent(id)}/acessos`)
      .then((r) => setA(Array.isArray(r?.acessos) ? r.acessos : [])).catch(() => setFalhou(true))
  }
  if (!pode) return null
  return (
    <section className="cartao portal-bloco" aria-labelledby="t-acessos">
      <h3 id="t-acessos">Quem consultou estes dados</h3>
      {a === null ? (
        <>
          <p className="sutil">A lista também fica registrada na auditoria.</p>
          <button className="btn secundario" onClick={ver}>Ver acessos</button>
          {falhou && <p className="erro-texto">Não foi possível carregar os acessos.</p>}
        </>
      ) : a.length === 0 ? <p className="sutil">Nenhum acesso registrado.</p> : (
        <ul className="acessos">
          {a.slice(0, 20).map((x, i) => <li key={`${x.quando}-${i}`}><span>{x.usuario ?? '—'} · {x.o_que}</span><span className="sutil num">{dataHoraBR(x.quando)}</span></li>)}
        </ul>
      )}
    </section>
  )
}

export { estadoArea, ImpostosCidadao }

import { useCallback, useEffect, useState } from 'react'
import { api, dataBR, dataHoraBR } from './api'
import { useAuth } from './auth'
import ImpostosCidadao, { reais, validaTributario, type RespTributario } from './ImpostosCidadao'

type Proximo = { data: string | null; unidade: string | null; especialidade: string | null; dias_espera: number | null }
type AtendS = { data: string | null; unidade: string | null; especialidade: string | null }
type VacS = { data: string | null; vacina: string | null; dose: string | null; unidade: string | null }
export type RespSaude = { cidadao_id: string; proximos: Proximo[]; atendimentos: AtendS[]; vacinas: VacS[] }
type EventoT = { data: string; area: string; tipo: string; titulo: string; fonte: string }
type Acesso = { usuario: string | null; acao: string; o_que: string; quando: string }

const validaSaude = (r: unknown): r is RespSaude => !!r && Array.isArray((r as RespSaude).proximos)
  && Array.isArray((r as RespSaude).atendimentos) && Array.isArray((r as RespSaude).vacinas)
const AREA: Record<string, string> = { saude: 'Saúde', impostos: 'Impostos', familia: 'Família', pedidos: 'Pedidos' }

/** FC1: busca impostos e saúde UMA vez (cada consulta é auditada) e entrega aos blocos da ficha. */
export function useDadosFicha(id: string) {
  const [trib, setTrib] = useState<RespTributario | null | false>(null)
  const [saude, setSaude] = useState<RespSaude | null | false>(null)
  useEffect(() => {
    setTrib(null); setSaude(null)
    const c = encodeURIComponent(id)
    api<unknown>(`/gold/cidadaos/${c}/tributario`).then((r) => setTrib(validaTributario(r) ? r : false)).catch(() => setTrib(false))
    api<unknown>(`/gold/cidadaos/${c}/saude`).then((r) => setSaude(validaSaude(r) ? r : false)).catch(() => setSaude(false))
  }, [id])
  return { trib, saude }
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
    out.push({ area: 'Saúde', titulo: `Consulta ${p.especialidade ? `de ${p.especialidade} ` : ''}em ${dataBR(p.data)}`, detalhe: p.unidade ?? 'Unidade de saúde', tom: 'info' })
  }
  return out
}

/** FC1: "Para fazer agora" + resumo em cartões, como no portal do canvas. */
export function ResumoPortal({ trib, saude }: { trib: RespTributario | null | false; saude: RespSaude | null | false }) {
  if (trib === null || saude === null) return <section className="cartao portal-resumo"><p className="sutil">Carregando resumo…</p></section>
  const lista = pendencias(trib, saude)
  const t = trib ? trib.totais : null
  return (
    <section className="portal-resumo" aria-label="Resumo do cidadão">
      <div className="cartao portal-pendencias">
        <h3>Para fazer agora</h3>
        {lista.length === 0 ? <p className="sutil">Nenhuma pendência encontrada nas bases integradas.</p> : (
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
      <div className="portal-cartoes">
        <div className="cartao"><span className="cartao-rotulo">Imóveis</span><span className="cartao-valor num">{t ? t.imoveis : '—'}</span></div>
        <div className="cartao"><span className="cartao-rotulo">Em aberto</span><span className={`cartao-valor num${t && t.em_aberto > 0 ? ' perda' : ''}`}>{t ? reais(t.em_aberto) : '—'}</span></div>
        <div className="cartao"><span className="cartao-rotulo">Próxima consulta</span><span className="cartao-valor num">{saude && saude.proximos[0] ? dataBR(saude.proximos[0].data) : '—'}</span></div>
        <div className="cartao"><span className="cartao-rotulo">Vacinas registradas</span><span className="cartao-valor num">{saude ? saude.vacinas.length : '—'}</span></div>
      </div>
    </section>
  )
}

/** FC2: relacionamento com a Saúde, sem conteúdo clínico. */
export function SaudeCidadao({ saude }: { saude: RespSaude | null | false }) {
  return (
    <section className="cartao portal-bloco" aria-labelledby="t-saude-cid">
      <h3 id="t-saude-cid">Saúde</h3>
      {saude === null ? <p className="sutil">Carregando…</p> : saude === false ? <p className="sutil">Sem leitura dos dados de saúde.</p> : (
        <>
          <h4>Próximos compromissos</h4>
          {saude.proximos.length === 0 ? <p className="sutil">Nenhuma consulta agendada.</p> : (
            <ul className="agenda-lista">
              {saude.proximos.map((p) => (
                <li key={`${p.data}-${p.unidade}-${p.especialidade}`}>
                  <span className="dia num">{dataBR(p.data)}</span>
                  <span><strong>{p.especialidade ?? 'Consulta'}</strong><span className="sutil"> · {p.unidade ?? '—'}{p.dias_espera != null ? ` · ${p.dias_espera} dias de espera` : ''}</span></span>
                </li>
              ))}
            </ul>
          )}
          <h4>Últimos atendimentos</h4>
          {saude.atendimentos.length === 0 ? <p className="sutil">Nenhum atendimento nos últimos 12 meses.</p> : (
            <ul className="agenda-lista">
              {saude.atendimentos.slice(0, 5).map((a, i) => (
                <li key={`${a.data}-${i}`}><span className="dia num">{dataBR(a.data)}</span><span>{a.especialidade ?? 'Atendimento'}<span className="sutil"> · {a.unidade ?? '—'}</span></span></li>
              ))}
            </ul>
          )}
          <h4>Vacinas</h4>
          {saude.vacinas.length === 0 ? <p className="sutil">Nenhuma vacina nos últimos 24 meses.</p> : (
            <div className="chips-vacina">
              {saude.vacinas.map((v, i) => <span key={`${v.vacina}-${v.data}-${i}`} className="selo sucesso"><i aria-hidden="true" />{v.vacina}{v.dose ? ` ${v.dose}` : ''} · {dataBR(v.data)}</span>)}
            </div>
          )}
          <p className="nota">Diagnósticos e anotações clínicas não aparecem aqui.</p>
        </>
      )}
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

export { ImpostosCidadao }

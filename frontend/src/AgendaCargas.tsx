import { useCallback, useEffect, useState } from 'react'
import { api, ApiErro, dataHoraBR } from './api'

type ExecAg = { id: number; status: string; etapa: string | null; iniciado_em: string | null; finalizado_em: string | null
  lidos: number; gravados: number; processadas_etapa: number | null; total_etapa: number | null }
type ItemAgenda = {
  conector: string; nome: string; horario: string; ativo: boolean; ordem: number; forcar: boolean
  ultimo_status: string | null; ultimo_status_texto: string | null; ultima_mensagem: string | null
  ultimo_inicio_em: string | null; ultimo_fim_em: string | null; tentativas: number; proxima_em: string | null
  horario_fora_da_janela: boolean; ultima_execucao: ExecAg | null; em_andamento: ExecAg | null
}
export type RespAgenda = {
  habilitada: boolean; janela: string; dia_todo: boolean; fuso: string; pular_sem_mudanca: boolean; alerta_email: boolean
  agora: string; itens: ItemAgenda[]; historico: (ExecAg & { conector: string })[]
}
export const AGENDA_INTERVALO_MS = 30000

const valida = (r: unknown): r is RespAgenda =>
  !!r && Array.isArray((r as RespAgenda).itens) && Array.isArray((r as RespAgenda).historico) && typeof (r as RespAgenda).janela === 'string'

/** Classe do selo pelo status do agendamento (mesmas cores do resto do sistema). */
export function classeStatus(s: string | null): string {
  if (s === 'sucesso') return 'sucesso'
  if (s === 'em_andamento') return 'em_andamento'
  if (s === 'falha' || s === 'interrompida' || s === 'demorada') return 'falha'
  if (s === 'bloqueada' || s === 'ocupada' || s === 'pausada' || s === 'cancelada') return 'atencao'
  return 'neutro'
}
const ETAPA: Record<string, string> = { leitura: 'Lendo da origem', prata: 'Tratando (prata)', ouro: 'Unificando cidadãos (ouro)' }
const hora = (iso: string | null) => iso
  ? new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso))
  : '—'
const n = (v: number) => new Intl.NumberFormat('pt-BR').format(v)

/** AG1–AG6: agenda das cargas completas (todos veem; só admin altera e executa agora). Atualiza a cada 30 s. */
export default function AgendaCargas({ ehAdmin }: { ehAdmin: boolean }) {
  const [d, setD] = useState<RespAgenda | null>(null)
  const [falhou, setFalhou] = useState(false)
  const [aviso, setAviso] = useState('')
  const [salvando, setSalvando] = useState<string | null>(null)

  const ler = useCallback(() => {
    api<unknown>('/agenda').then((r) => {
      if (valida(r)) { setD(r); setFalhou(false) } else setFalhou(true)
    }).catch(() => setFalhou(true))
  }, [])
  useEffect(() => {
    ler()
    const t = setInterval(ler, AGENDA_INTERVALO_MS)
    return () => clearInterval(t)
  }, [ler])

  const enviar = async (conector: string, caminho: string, init: RequestInit, ok: string) => {
    setSalvando(conector); setAviso('')
    try {
      const r = await api<unknown>(caminho, init)
      if (valida(r)) setD(r)
      setAviso(ok)
    } catch (e) {
      setAviso(e instanceof ApiErro ? e.message : 'Não foi possível salvar a agenda.')
    } finally {
      setSalvando(null)
    }
  }
  const alterar = (i: ItemAgenda, dados: { horario?: string; ativo?: boolean }) =>
    enviar(i.conector, `/agenda/${encodeURIComponent(i.conector)}`, { method: 'PUT', body: JSON.stringify(dados) },
      dados.ativo === undefined ? `Horário de ${i.nome} salvo.` : `Agenda de ${i.nome} ${dados.ativo ? 'ligada' : 'desligada'}.`)
  const agora = (i: ItemAgenda) =>
    enviar(i.conector, `/agenda/${encodeURIComponent(i.conector)}/executar-agora`, { method: 'POST' },
      `${i.nome} entra na fila da agenda e começa em até 1 minuto.`)

  if (falhou || !d) {
    return (
      <section className="cartao agenda" aria-labelledby="t-agenda">
        <h2 id="t-agenda">Agenda das cargas</h2>
        <p className="sutil">{falhou ? 'Sem leitura da agenda.' : 'Carregando agenda…'}</p>
      </section>
    )
  }

  const janela = d.dia_todo ? 'o dia todo' : d.janela.replace('-', ' às ')
  return (
    <section className="cartao agenda" aria-labelledby="t-agenda">
      <div className="agenda-cab">
        <div>
          <h2 id="t-agenda">Agenda das cargas</h2>
          <p className="sutil">Uma carga por vez, {d.dia_todo ? 'a qualquer hora' : `só entre ${janela}`}
            {d.pular_sem_mudanca ? ' · pula o conector quando a origem não mudou' : ''}.</p>
        </div>
        <span className={`selo ${d.habilitada ? 'sucesso' : 'neutro'}`}><i aria-hidden="true" />
          {d.habilitada ? `Ligada · ${d.dia_todo ? 'dia todo' : janela}` : 'Desligada neste ambiente'}</span>
      </div>
      {!d.habilitada && (
        <p className="aviso-ambar">A agenda não roda neste ambiente. Para ligar, defina <code>AGENDA_HABILITADA=true</code> no
          Coolify e reinicie o backend. Os horários abaixo já podem ser preparados.</p>
      )}

      <table className="tabela agenda-tabela">
        <caption className="sr-only">Agenda das cargas completas</caption>
        <thead><tr><th scope="col">Conector</th><th scope="col">Horário</th><th scope="col">Agenda</th><th scope="col">Próxima</th><th scope="col">Última carga</th>{ehAdmin && <th scope="col"><span className="sr-only">Ações</span></th>}</tr></thead>
        <tbody>
          {d.itens.map((i) => {
            const rodando = i.em_andamento
            const ocupado = salvando === i.conector
            return (
              <tr key={i.conector}>
                <th scope="row" data-rotulo="Conector">{i.nome}</th>
                <td data-rotulo="Horário">
                  {ehAdmin
                    ? <input type="time" className="agenda-hora" aria-label={`Horário da carga de ${i.nome}`} defaultValue={i.horario}
                        key={`${i.conector}-${i.horario}`} disabled={ocupado}
                        onBlur={(e) => { if (e.target.value && e.target.value !== i.horario) alterar(i, { horario: e.target.value }) }} />
                    : <span className="num">{i.horario}</span>}
                  {i.horario_fora_da_janela && !d.dia_todo && <span className="agenda-nota">fora da janela</span>}
                </td>
                <td data-rotulo="Agenda">
                  {ehAdmin
                    ? <label className="agenda-chave">
                        <input type="checkbox" checked={i.ativo} disabled={ocupado} onChange={(e) => alterar(i, { ativo: e.target.checked })} />
                        <span>{i.ativo ? 'Ligada' : 'Desligada'}</span>
                        <span className="sr-only"> para {i.nome}</span>
                      </label>
                    : <span>{i.ativo ? 'Ligada' : 'Desligada'}</span>}
                </td>
                <td data-rotulo="Próxima">{i.forcar ? 'Em até 1 minuto' : i.proxima_em && d.habilitada ? dataHoraBR(i.proxima_em) : '—'}</td>
                <td data-rotulo="Última carga">
                  {rodando
                    ? <div className="agenda-ultima">
                        <span className="selo em_andamento"><i aria-hidden="true" />{ETAPA[rodando.etapa ?? ''] ?? 'Em andamento'}</span>
                        {rodando.total_etapa ? <span className="sutil num">{n(rodando.processadas_etapa ?? 0)} de {n(rodando.total_etapa)}</span> : null}
                      </div>
                    : i.ultimo_status
                      ? <div className="agenda-ultima">
                          <span className={`selo ${classeStatus(i.ultimo_status)}`}><i aria-hidden="true" />{i.ultimo_status_texto}</span>
                          <span className="sutil">{dataHoraBR(i.ultimo_fim_em ?? i.ultimo_inicio_em)}{i.tentativas > 0 ? ` · tentativa ${i.tentativas}` : ''}</span>
                          {i.ultima_mensagem && <span className="agenda-msg">{i.ultima_mensagem}</span>}
                        </div>
                      : <span className="sutil">Ainda não rodou</span>}
                </td>
                {ehAdmin && (
                  <td data-rotulo="Ações">
                    <button className="btn secundario" onClick={() => agora(i)} disabled={!d.habilitada || !!rodando || i.forcar || ocupado}
                      title={!d.habilitada ? 'Agenda desligada neste ambiente' : rodando ? 'Carga em andamento' : undefined}>
                      Executar agora
                    </button>
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
      <div aria-live="polite">{aviso && <p className="aviso">{aviso}</p>}</div>
      <LinhaDoTempo d={d} />
    </section>
  )
}

/** Gantt das execuções da agenda nas últimas 24 h (posição e largura pelo horário real). */
function LinhaDoTempo({ d }: { d: RespAgenda }) {
  const fim = new Date(d.agora).getTime()
  const ini = fim - 24 * 3600 * 1000
  const pos = (iso: string | null) => (iso ? Math.max(0, Math.min(100, ((new Date(iso).getTime() - ini) / (fim - ini)) * 100)) : 100)
  const conectores = d.itens.map((i) => ({ id: i.conector, nome: i.nome }))
  const marcas = Array.from({ length: 5 }, (_, k) => new Date(ini + (k * 6 * 3600 * 1000)).toISOString())
  const resumo = d.historico.length
    ? `${d.historico.length} execução(ões) da agenda nas últimas 24 horas`
    : 'Nenhuma execução da agenda nas últimas 24 horas'
  return (
    <div className="agenda-tempo">
      <h3>Últimas 24 horas</h3>
      {d.historico.length === 0 ? <p className="sutil">{resumo}.</p> : (
        <>
          <div role="img" aria-label={resumo} className="gantt">
            <div className="gantt-eixo" aria-hidden="true"><span />{marcas.map((m) => <span key={m}>{hora(m)}</span>)}</div>
            {conectores.map((c) => (
              <div key={c.id} className="gantt-linha" aria-hidden="true">
                <span className="gantt-rotulo">{c.nome}</span>
                <div className="gantt-trilho">
                  {d.historico.filter((h) => h.conector === c.id).map((h) => {
                    const x = pos(h.iniciado_em)
                    const w = Math.max(0.8, pos(h.finalizado_em ?? d.agora) - x)
                    return <span key={h.id} className={`gantt-barra ${classeStatus(h.status)}`} style={{ left: `${x}%`, width: `${w}%` }} />
                  })}
                </div>
              </div>
            ))}
          </div>
          <ul className="sr-only">
            {d.historico.map((h) => (
              <li key={h.id}>{conectores.find((c) => c.id === h.conector)?.nome ?? h.conector}: {h.status}, de {hora(h.iniciado_em)} a {hora(h.finalizado_em)}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

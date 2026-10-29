import { useCallback, useEffect, useState } from 'react'
import { api, ApiErro } from './api'

type ExecAg = { id: number; status: string; etapa: string | null; modo?: string; iniciado_em: string | null; finalizado_em: string | null
  lidos: number; gravados: number; processadas_etapa: number | null; total_etapa: number | null }
type ItemAgenda = {
  conector: string; nome: string; horario: string; ativo: boolean; ordem: number; forcar: boolean
  ultimo_status: string | null; ultimo_status_texto: string | null; ultima_mensagem: string | null
  ultimo_inicio_em: string | null; ultimo_fim_em: string | null; tentativas: number; proxima_em: string | null
  horario_fora_da_janela: boolean; ultima_execucao: ExecAg | null; em_andamento: ExecAg | null
  proximo_modo?: string; ultima_completa_em?: string | null
  ultima_execucao_real?: ExecAg | null; pode_executar_agora?: boolean; motivo_executar_agora?: string | null
}
export type RespAgenda = {
  habilitada: boolean; janela: string; dia_todo: boolean; fuso: string; pular_sem_mudanca: boolean; alerta_email: boolean
  completa_a_cada_dias?: number
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
export const EDUCACAO = 'educacao'
export const STATUS_EXEC: Record<string, string> = { sucesso: 'Concluída', falha: 'Falhou', parcial: 'Parcial', em_andamento: 'Em andamento', sem_novidades: 'Sem novidades' }
export const MODO: Record<string, string> = { completa: 'Completa', incremental: 'Incremental' }
const ETAPA: Record<string, string> = { leitura: 'Lendo da origem', prata: 'Tratando (prata)', ouro: 'Unificando cidadãos (ouro)' }
const hora = (iso: string | null) => iso
  ? new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso))
  : '—'
const n = (v: number) => new Intl.NumberFormat('pt-BR').format(v)
const curta = (iso: string) => new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso)).replace(',', '')

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
            {d.pular_sem_mudanca ? ' · pula o conector quando a origem não mudou' : ''}
            {d.completa_a_cada_dias ? ` · lê só o que mudou e faz uma carga completa a cada ${d.completa_a_cada_dias} dias` : ''}.</p>
        </div>
        <span className={`selo ${d.habilitada ? 'sucesso' : 'neutro'}`}><i aria-hidden="true" />
          {d.habilitada ? `Ligada · ${d.dia_todo ? 'dia todo' : janela}` : 'Desligada neste ambiente'}</span>
      </div>
      {!d.habilitada && (
        <p className="aviso-ambar">A agenda não roda neste ambiente. Para ligar, defina <code>AGENDA_HABILITADA=true</code> no
          Coolify e reinicie o backend. Os horários abaixo já podem ser preparados.</p>
      )}

      <div className="agenda-rolagem">
      <table className="tabela agenda-tabela">
        <caption className="sr-only">Agenda das cargas completas</caption>
        <thead><tr><th scope="col">Conector</th><th scope="col">Horário</th><th scope="col">Próxima</th><th scope="col">Última carga</th>{ehAdmin && <th scope="col"><span className="sr-only">Ações</span></th>}</tr></thead>
        <tbody>
          {d.itens.filter((i) => i.conector !== EDUCACAO).map((i) => {
            const rodando = i.em_andamento
            const ocupado = salvando === i.conector
            return (
              <tr key={i.conector}>
                <th scope="row" data-rotulo="Conector">{i.nome}</th>
                <td data-rotulo="Horário">
                  <div className="agenda-horario">
                    {ehAdmin
                      ? <input type="time" className="agenda-hora" aria-label={`Horário da carga de ${i.nome}`} defaultValue={i.horario}
                          key={`${i.conector}-${i.horario}`} disabled={ocupado}
                          onBlur={(e) => { if (e.target.value && e.target.value !== i.horario) alterar(i, { horario: e.target.value }) }} />
                      : <span className="num">{i.horario}</span>}
                    {ehAdmin
                      ? <label className="agenda-chave">
                          <input type="checkbox" checked={i.ativo} disabled={ocupado} onChange={(e) => alterar(i, { ativo: e.target.checked })} />
                          <span>{i.ativo ? 'Ligada' : 'Desligada'}</span>
                          <span className="sr-only"> para {i.nome}</span>
                        </label>
                      : <span className="sutil">{i.ativo ? 'Ligada' : 'Desligada'}</span>}
                    {i.horario_fora_da_janela && !d.dia_todo && <span className="agenda-nota">fora da janela</span>}
                  </div>
                </td>
                <td data-rotulo="Próxima">
                  <div className="agenda-ultima">
                    <span>{i.forcar ? 'Em até 1 minuto' : i.proxima_em && d.habilitada ? curta(i.proxima_em) : '—'}</span>
                    {i.ativo && i.proximo_modo && <span className="agenda-modo">{MODO[i.proximo_modo] ?? i.proximo_modo}</span>}
                  </div>
                </td>
                <td data-rotulo="Última carga">
                  {rodando
                    ? <div className="agenda-ultima">
                        <span className="selo em_andamento"><i aria-hidden="true" />{ETAPA[rodando.etapa ?? ''] ?? 'Em andamento'}</span>
                        {rodando.total_etapa ? <span className="sutil num">{n(rodando.processadas_etapa ?? 0)} de {n(rodando.total_etapa)}</span> : null}
                      </div>
                    : i.ultimo_status
                      ? <div className="agenda-ultima">
                          <span className={`selo ${classeStatus(i.ultimo_status)}`}><i aria-hidden="true" />{i.ultimo_status_texto}</span>
                          {i.ultima_execucao?.modo && <span className="agenda-modo">{MODO[i.ultima_execucao.modo] ?? i.ultima_execucao.modo}</span>}
                          <span className="sutil">{curta(i.ultimo_fim_em ?? i.ultimo_inicio_em ?? d.agora)}{i.tentativas > 0 ? ` · tentativa ${i.tentativas}` : ''}</span>
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
      </div>
      <div aria-live="polite">{aviso && <p className="aviso">{aviso}</p>}</div>
      {d.itens.filter((i) => i.conector === EDUCACAO).map((i) => (
        <CartaoEducacao key={i.conector} i={i} d={d} ehAdmin={ehAdmin} ocupado={salvando === i.conector}
          alterar={(dados) => alterar(i, dados)} executar={() => agora(i)} />
      ))}
      <LinhaDoTempo d={d} />
    </section>
  )
}

/** Educação (GestaoEscolar + CoreSSO) entre as fontes prioritárias: mesma agenda (PUT/POST) das outras fontes. */
export function CartaoEducacao({ i, d, ehAdmin, ocupado, alterar, executar }: {
  i: ItemAgenda; d: RespAgenda; ehAdmin: boolean; ocupado: boolean
  alterar: (dados: { horario?: string; ativo?: boolean }) => void; executar: () => void
}) {
  const ult = i.ultima_execucao_real ?? null
  const rodando = i.em_andamento
  const motivo = !d.habilitada ? 'A agenda está desligada neste ambiente.'
    : !i.ativo ? 'Ligue a agenda para executar agora.'
    : rodando ? 'Carga em andamento.'
    : i.forcar ? 'Já está na fila: começa em até 1 minuto.'
    : i.pode_executar_agora === false ? (i.motivo_executar_agora ?? 'Indisponível agora.') : null
  const proxima = i.ativo && d.habilitada && (i.forcar || i.proxima_em)
    ? `Próxima execução: ${i.forcar ? 'em até 1 minuto' : curta(i.proxima_em as string)}` : 'Sem agendamento'
  return (
    <article className="cartao educacao-cartao" aria-labelledby="t-edu">
      <div className="agenda-cab">
        <h3 id="t-edu">{i.nome}</h3>
        <span className={`selo ${i.ativo ? 'sucesso' : 'neutro'}`}><i aria-hidden="true" />{i.ativo ? 'Agenda ligada' : 'Agenda desligada'}</span>
      </div>
      <dl className="educacao-dados">
        <div><dt>Última execução</dt><dd>
          {rodando
            ? <span className="selo em_andamento"><i aria-hidden="true" />Em andamento · desde {curta(rodando.iniciado_em ?? d.agora)}</span>
            : ult
              ? <span className="agenda-ultima">
                  <span className={`selo ${classeStatus(ult.status)}`}><i aria-hidden="true" />{STATUS_EXEC[ult.status] ?? ult.status}</span>
                  <span className="sutil">{curta(ult.finalizado_em ?? ult.iniciado_em ?? d.agora)} · {n(ult.lidos ?? 0)} {ult.lidos === 1 ? 'linha lida' : 'linhas lidas'}</span>
                </span>
              : <span className="sutil">Ainda não rodou</span>}
        </dd></div>
        <div><dt>Frequência</dt><dd>Diária</dd></div>
        <div><dt>Horário</dt><dd>
          {ehAdmin
            ? <input type="time" className="agenda-hora" aria-label={`Horário da carga de ${i.nome}`} defaultValue={i.horario}
                key={`edu-${i.horario}`} disabled={ocupado}
                onBlur={(e) => { if (e.target.value && e.target.value !== i.horario) alterar({ horario: e.target.value }) }} />
            : <span className="num">{i.horario}</span>}
        </dd></div>
        <div><dt>Agenda</dt><dd>
          {ehAdmin
            ? <label className="agenda-chave">
                <input type="checkbox" role="switch" checked={i.ativo} disabled={ocupado} onChange={(e) => alterar({ ativo: e.target.checked })} />
                <span>{i.ativo ? 'Ligada' : 'Desligada'}</span><span className="sr-only"> para {i.nome}</span>
              </label>
            : <span>{i.ativo ? 'Ligada' : 'Desligada'}</span>}
        </dd></div>
      </dl>
      <p className="sutil">{proxima}</p>
      {ehAdmin && (
        <div className="acoes">
          <button className="btn secundario" onClick={executar} disabled={!!motivo || ocupado}
            aria-describedby={motivo ? 'edu-motivo' : undefined}>Executar agora</button>
          {motivo && <span id="edu-motivo" className="sutil">{motivo}</span>}
        </div>
      )}
    </article>
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

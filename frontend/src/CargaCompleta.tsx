import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiErro, dataHoraBR } from './api'

const NOMES: Record<string, string> = { saude: 'Saúde', cemiterio: 'Cemitério' }
const milhar = (n: number | null | undefined) => (n == null ? '—' : new Intl.NumberFormat('pt-BR').format(n))

export type ExecCompleta = {
  id: number; status: string; usuario: string | null; iniciado_em: string; finalizado_em: string | null
  lidos: number; novos: number; atualizados: number; ignorados: number; lotes: number; gravados: number
  total_origem: number | null; processados: number; tabela_atual: string | null; pausa_motivo: string | null
  cancelamento_pedido: boolean; mensagem: string | null; erro: { motivo: string } | null
  status_texto?: string; etapa?: Etapa | null; etapa_nome?: string | null; processadas_etapa?: number | null
  tabelas_lidas?: number | null; tabelas_total?: number | null
  total_etapa?: number | null; atualizado_em?: string | null; sem_progresso?: boolean; alerta_min?: number
  pode_retomar_camadas?: boolean
}
export type Etapa = 'leitura' | 'prata' | 'ouro' | 'concluida'
export const ETAPAS: { id: Etapa; nome: string; contador: string }[] = [
  { id: 'leitura', nome: 'Lendo da origem', contador: 'Leitura' },
  { id: 'prata', nome: 'Tratando (prata)', contador: 'Prata' },
  { id: 'ouro', nome: 'Unificando cidadãos (ouro)', contador: 'Ouro' },
  { id: 'concluida', nome: 'Concluída', contador: '' },
]
export type Situacao = {
  conector: string; liberada: boolean; motivo: string | null; execucao: ExecCompleta | null
  camadas: { bronze: number | null; prata: number | null; pendencias: number | null; gerados_prata?: number | null }
  reconciliacao: Record<string, { origem: number | null; bronze: number; quarentena?: number; ok: boolean; status?: string }> | null
  quarentena?: number | null
}
type LinhaQuarentena = { id: number; tabela: string; motivo: string; campos_nulos: string[]; referencia: string; extraido_em: string | null }
type Estimativa = { tabelas: Record<string, number>; total: number }

export const INTERVALO_MS = 5000
export const ERRO_ATUALIZAR = 'Não foi possível atualizar o andamento da carga. Tentando de novo…'
const hora = (v?: string | null) => (v ? dataHoraBR(v).split(' ')[1] ?? '—' : '—')
function duracao(de?: string | null): string {
  if (!de) return '—'
  const ms = Date.now() - new Date(de).getTime()
  if (!(ms >= 0)) return '—'
  const m = Math.floor(ms / 60000)
  return m < 1 ? 'menos de 1 min' : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`
}
export const ESTIMATIVA_FALHOU = 'Não foi possível estimar as linhas da origem. Feche e tente de novo.'

/** Carga completa REAL por conector (CC1-CC6): confirmação com estimativa, progresso, cancelar, card final. */
export default function CargaCompleta({ conector, liberada, motivo, onFim, onRodando }: {
  conector: string; liberada: boolean; motivo: string | null; onFim: () => void; onRodando?: (r: boolean) => void
}) {
  const nome = NOMES[conector] ?? conector
  const [sit, setSit] = useState<Situacao | null>(null)
  const [modal, setModal] = useState(false)
  const [est, setEst] = useState<Estimativa | null>(null)
  const [estErro, setEstErro] = useState('')
  const [erro, setErro] = useState('')
  const [toast, setToast] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [erroPoll, setErroPoll] = useState('')
  const [confRetomar, setConfRetomar] = useState(false)
  const [retomando, setRetomando] = useState(false)
  const cancelarRetomar = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!confRetomar) return
    cancelarRetomar.current?.focus()
    const f = (e: KeyboardEvent) => { if (e.key === 'Escape') setConfRetomar(false) }
    document.addEventListener('keydown', f)
    return () => document.removeEventListener('keydown', f)
  }, [confRetomar])
  const [quar, setQuar] = useState<LinhaQuarentena[] | null>(null)
  const [quarErro, setQuarErro] = useState('')
  const verQuarentena = async () => {
    if (quar) { setQuar(null); return }
    setQuarErro('')
    try {
      setQuar((await api<{ linhas: LinhaQuarentena[] }>(`/conectores/${conector}/completa/quarentena`)).linhas)
    } catch (e) {
      setQuarErro(e instanceof ApiErro && e.message ? e.message : 'Não foi possível carregar a quarentena.')
    }
  }
  const anterior = useRef<string | null>(null)
  const cancelarModal = useRef<HTMLButtonElement>(null)

  const carregar = useCallback(async () => {
    try {
      const s = await api<Situacao>(`/conectores/${conector}/completa`)
      setSit(s)
      setErroPoll('')
      return s
    } catch (e) {
      setErroPoll(e instanceof ApiErro && e.message ? `${ERRO_ATUALIZAR} (${e.message})` : ERRO_ATUALIZAR)
      return null
    }
  }, [conector])
  useEffect(() => { void carregar() }, [carregar])

  const status = sit?.execucao?.status ?? null
  const rodando = status === 'em_andamento'
  useEffect(() => { onRodando?.(rodando) }, [rodando, onRodando])
  useEffect(() => {
    if (anterior.current === 'em_andamento' && status && status !== 'em_andamento') {
      if (status === 'sucesso') setToast(`Carga completa de ${nome} concluída: ${milhar(sit?.execucao?.lidos)} registros lidos.`)
      else onFim()
    }
    anterior.current = status
  }, [status, nome, sit, onFim])
  useEffect(() => {
    if (!rodando) return
    const t = setInterval(() => { void carregar() }, INTERVALO_MS)
    return () => clearInterval(t)
  }, [rodando, carregar])
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => { setToast(''); onFim() }, 6000)  // depois do toast, atualiza as contagens do painel
    return () => clearTimeout(t)
  }, [toast, onFim])
  useEffect(() => {
    if (!modal) return
    cancelarModal.current?.focus()  // foco no Cancelar (ação segura)
    const f = (e: KeyboardEvent) => { if (e.key === 'Escape') setModal(false) }
    document.addEventListener('keydown', f)
    return () => document.removeEventListener('keydown', f)
  }, [modal])

  const abrir = async () => {
    setModal(true); setEst(null); setEstErro(''); setErro('')
    try {
      setEst(await api<Estimativa>(`/conectores/${conector}/completa/estimativa`))
    } catch (e) {
      setEstErro(e instanceof ApiErro && e.status === 409 && e.message ? e.message : ESTIMATIVA_FALHOU)
    }
  }
  const disparar = async () => {
    setModal(false); setErro(''); setEnviando(true)
    try {
      setSit(await api<Situacao>(`/conectores/${conector}/completa`, { method: 'POST' }))
      anterior.current = 'em_andamento'
    } catch (e) {
      setErro(e instanceof ApiErro ? e.message : 'Não foi possível iniciar a carga completa.')
    } finally {
      setEnviando(false)
    }
  }
  const retomarCamadas = async () => {
    setConfRetomar(false); setErro(''); setEnviando(true); setRetomando(true)
    try {
      setSit(await api<Situacao>(`/conectores/${conector}/completa/retomar-camadas`, { method: 'POST' }))
      anterior.current = 'em_andamento'
    } catch (e) {
      setErro(e instanceof ApiErro ? e.message : 'Não foi possível retomar a prata e o ouro.')
    } finally {
      setEnviando(false); setRetomando(false)
    }
  }
  const cancelar = async () => {
    try {
      setSit(await api<Situacao>(`/conectores/${conector}/completa/cancelar`, { method: 'POST' }))
    } catch (e) {
      setErro(e instanceof ApiErro ? e.message : 'Não foi possível cancelar.')
    }
  }

  const ok = (sit ? sit.liberada : liberada) && !rodando
  const porque = sit ? sit.motivo : motivo
  const ex = sit?.execucao
  const idMotivo = `motivo-completa-${conector}`

  return (
    <div className="carga-completa">
      {!rodando && (
        <span className="com-dica">
          <button className="btn outlined" disabled={!ok || enviando} aria-label={`Puxar carga completa de ${nome}`}
            aria-describedby={!ok && porque ? idMotivo : undefined} title={!ok && porque ? porque : undefined} onClick={abrir}>
            Carga completa
          </button>
          {!ok && porque && <span id={idMotivo} className="sutil motivo-desabilitado"> {porque}</span>}
        </span>
      )}

      {confRetomar && (
        <div className="veu-dialogo" onClick={(e) => { if (e.target === e.currentTarget) setConfRetomar(false) }}>
          <div role="dialog" aria-modal="true" aria-labelledby={`t-rc-${conector}`} className="dialogo">
            <h3 id={`t-rc-${conector}`}>Reprocessar prata e ouro a partir da bronze já carregada? A origem não será consultada.</h3>
            <div className="acoes">
              <button ref={cancelarRetomar} className="btn secundario" onClick={() => setConfRetomar(false)}>Cancelar</button>
              <button className="btn" onClick={retomarCamadas}>Reprocessar prata e ouro</button>
            </div>
          </div>
        </div>
      )}

      {modal && (
        <div className="veu-dialogo" onClick={(e) => { if (e.target === e.currentTarget) setModal(false) }}>
          <div role="dialog" aria-modal="true" aria-labelledby={`t-cc-${conector}`} className="dialogo">
            <h3 id={`t-cc-${conector}`}>Carregar todos os registros de {nome}? Isso pode levar alguns minutos.</h3>
            {est ? (
              <p>A origem tem <strong className="num">{milhar(est.total)}</strong> linhas em {Object.keys(est.tabelas).length} tabelas.
                A leitura é só de consulta, em lotes; nada é apagado e dá para cancelar.</p>
            ) : estErro ? <p className="erro-texto">{estErro}</p> : <p aria-busy="true">Contando as linhas…</p>}
            <div className="acoes">
              <button ref={cancelarModal} className="btn secundario" onClick={() => setModal(false)}>Cancelar</button>
              <button className="btn" onClick={disparar} disabled={!est}>Iniciar carga completa</button>
            </div>
          </div>
        </div>
      )}

      {rodando && ex && <Andamento ex={ex} nome={nome} onCancelar={cancelar} />}
      {erroPoll && rodando && <p role="alert" className="aviso erro">{erroPoll}</p>}

      {erro && (
        <div role="alert" className="aviso erro">
          <p>{erro}</p>
          <button className="btn" onClick={abrir}>Tentar de novo</button>
        </div>
      )}

      {ex && !rodando && ex.status === 'falha' && !erro && (
        <div role="alert" className="aviso erro">
          <p>{ex.mensagem}</p>
          <button className="btn" onClick={abrir} disabled={!ok}>Tentar de novo</button>
        </div>
      )}
      {ex && !rodando && ex.status === 'interrompida' && (
        <div className="aviso-ambar" role="status">
          <p><strong>Carga interrompida</strong>{ex.etapa_nome ? ` durante: ${ex.etapa_nome}` : ''}. {ex.mensagem}</p>
          {ex.pode_retomar_camadas && (
            <button className="btn" onClick={() => setConfRetomar(true)} disabled={enviando || retomando}>
              {retomando ? 'Retomando…' : 'Retomar prata e ouro'}
            </button>
          )}
          <button className="btn outlined" onClick={abrir} disabled={!ok}>Iniciar carga de novo</button>
        </div>
      )}
      {ex && !rodando && ex.status === 'pausada' && (
        <div className="aviso-ambar" role="status">
          {/* CC12 (Designer): segue pelos checkpoints de cada tabela, sem reler o que já foi lido */}
          <p><strong>Carga pausada em {dataHoraBR(ex.finalizado_em ?? ex.atualizado_em ?? ex.iniciado_em)}.</strong> Ao continuar, ela segue de onde parou
            {ex.tabelas_total != null && <> ({ex.tabelas_lidas ?? 0} de {ex.tabelas_total} tabelas já lidas)</>}.</p>
          {ex.pausa_motivo && <p className="sutil">{ex.pausa_motivo}</p>}
          <button className="btn" onClick={abrir} disabled={!ok}>Continuar carga completa</button>
        </div>
      )}
      {ex && !rodando && ex.status === 'cancelada' && (
        <div className="aviso-ambar" role="status">
          <p>{ex.mensagem}</p>
          <button className="btn" onClick={abrir} disabled={!ok}>Retomar carga</button>
        </div>
      )}

      {ex && ex.status === 'sucesso' && sit && (
        <div className="cartao final-carga" aria-label={`Resultado da carga completa de ${nome}`}>
          <p className="sutil">Última carga completa: {dataHoraBR(ex.finalizado_em)}{ex.usuario ? ` · ${ex.usuario}` : ''}</p>
          <dl className="camadas">
            <div><dt>Lidos</dt><dd>{milhar(ex.lidos)}</dd></div>
            <div><dt>Gravados</dt><dd>{milhar(ex.gravados)}</dd></div>
            <div><dt>Bronze</dt><dd>{milhar(sit.camadas.bronze)}</dd></div>
            <div><dt>Prata</dt><dd>{milhar(sit.camadas.prata)}</dd></div>
            {sit.camadas.gerados_prata != null && <div><dt>Registros gerados na prata</dt><dd>{milhar(sit.camadas.gerados_prata)}</dd></div>}
            <div><dt>Pendências</dt><dd>{milhar(sit.camadas.pendencias)}</dd></div>
          </dl>
          {(sit.quarentena ?? 0) > 0 && (
            <div className="aviso-ambar" role="status">
              <p>Concluída com {milhar(sit.quarentena)} linha(s) em quarentena (chave nula)</p>
              <button className="link-btn" aria-expanded={quar !== null} onClick={verQuarentena}>
                {quar ? 'Ocultar detalhes da quarentena' : 'Ver detalhes da quarentena'}
              </button>
              {quarErro && <p className="erro-texto">{quarErro}</p>}
              {quar && (
                <table className="tabela-quarentena">
                  <caption className="sr-only">Linhas em quarentena de {nome}</caption>
                  <thead><tr><th scope="col">Tabela</th><th scope="col">Motivo</th><th scope="col">Campos nulos</th><th scope="col">Referência</th></tr></thead>
                  <tbody>{quar.map((l) => (
                    <tr key={l.id}><td>{l.tabela}</td><td>{l.motivo}</td><td>{l.campos_nulos.join(', ')}</td><td><code>{l.referencia}</code></td></tr>
                  ))}</tbody>
                </table>
              )}
            </div>
          )}
          {sit.reconciliacao && (
            Object.values(sit.reconciliacao).every((r) => r.ok)
              ? <p><span className="selo pronto"><i aria-hidden="true" />
                {Object.values(sit.reconciliacao).some((r) => (r.quarentena ?? 0) > 0)
                  ? 'Bronze + quarentena batem com a origem (ok com pendências)' : 'Bronze bate com a origem'}</span></p>
              : <p className="aviso-ambar">Bronze diferente da origem em: {Object.entries(sit.reconciliacao).filter(([, r]) => !r.ok)
                .map(([t, r]) => `${t} (origem ${milhar(r.origem)}, bronze ${milhar(r.bronze)})`).join('; ')}</p>
          )}
        </div>
      )}

      <div aria-live="polite">{toast && <p className="aviso toast">{toast}</p>}</div>
    </div>
  )
}

/** CC7: etapas visíveis, etapa atual destacada, concluídas com ✓, contador da etapa, tempo e última atividade. */
function Andamento({ ex, nome, onCancelar }: { ex: ExecCompleta; nome: string; onCancelar: () => void }) {
  const atual = ex.etapa ?? 'leitura'
  const i = ETAPAS.findIndex((e) => e.id === atual)
  const def = ETAPAS[i] ?? ETAPAS[0]
  const proc = ex.processadas_etapa ?? (atual === 'leitura' ? ex.processados : null)
  const tot = ex.total_etapa ?? (atual === 'leitura' ? ex.total_origem : null)
  return (
    <div className="progresso-carga" role="status" aria-live="polite" aria-label={`Andamento da carga completa de ${nome}`}>
      <p><strong>{ex.status_texto ?? 'Em andamento · Carga completa'}</strong></p>
      <ol className="etapas-carga">
        {ETAPAS.map((e, k) => (
          <li key={e.id} className={k < i ? 'feita' : k === i ? 'atual' : 'futura'} aria-current={k === i ? 'step' : undefined}>
            {k < i && <span aria-hidden="true">✓ </span>}{e.nome}{k < i && <span className="sr-only"> (concluída)</span>}
          </li>
        ))}
      </ol>
      {atual === 'leitura'
        ? <p>Lendo da origem…{ex.tabela_atual && <span className="sutil"> · {ex.tabela_atual}</span>}</p>
        : <p>Leitura concluída · tratando os dados</p>}
      {def.contador && proc != null && (atual === 'prata'
        // CC11: uma unidade só (linhas da bronze tratadas), nunca acima do total
        ? <p className="num">Tratando: {milhar(tot != null ? Math.min(proc, tot) : proc)}{tot != null && <> de {milhar(tot)}</>} linhas da bronze</p>
        : <p className="num">{def.contador}: {milhar(proc)}{tot != null && <> de {milhar(tot)}</>}</p>
      )}
      <p className="sutil">Em processamento, números parciais</p>
      <p className="sutil">Decorrido: {duracao(ex.iniciado_em)} · Última atividade: {hora(ex.atualizado_em ?? ex.iniciado_em)}</p>
      {ex.sem_progresso && (
        <p className="aviso-ambar" role="alert">Sem progresso desde {hora(ex.atualizado_em ?? ex.iniciado_em)}
          {' '}(mais de {ex.alerta_min ?? 5} min). Última atividade: {def.nome}.</p>
      )}
      <button className="btn secundario" onClick={onCancelar} disabled={ex.cancelamento_pedido || atual !== 'leitura'}
        title={atual !== 'leitura' ? 'O cancelamento vale durante a leitura da origem' : undefined}>
        {ex.cancelamento_pedido ? 'Cancelando…' : 'Cancelar carga'}
      </button>
    </div>
  )
}

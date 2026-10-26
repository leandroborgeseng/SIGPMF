import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiErro, dataHoraBR } from './api'

const NOMES: Record<string, string> = { saude: 'Saúde', cemiterio: 'Cemitério' }
const milhar = (n: number | null | undefined) => (n == null ? '—' : new Intl.NumberFormat('pt-BR').format(n))

export type ExecCompleta = {
  id: number; status: string; usuario: string | null; iniciado_em: string; finalizado_em: string | null
  lidos: number; novos: number; atualizados: number; ignorados: number; lotes: number; gravados: number
  total_origem: number | null; processados: number; tabela_atual: string | null; pausa_motivo: string | null
  cancelamento_pedido: boolean; mensagem: string | null; erro: { motivo: string } | null
}
export type Situacao = {
  conector: string; liberada: boolean; motivo: string | null; execucao: ExecCompleta | null
  camadas: { bronze: number; prata: number; pendencias: number }
  reconciliacao: Record<string, { origem: number | null; bronze: number; ok: boolean }> | null
}
type Estimativa = { tabelas: Record<string, number>; total: number }

export const INTERVALO_MS = 2000

/** Carga completa REAL por conector (CC1-CC6): confirmação com estimativa, progresso, cancelar, card final. */
export default function CargaCompleta({ conector, liberada, motivo, onFim }: {
  conector: string; liberada: boolean; motivo: string | null; onFim: () => void
}) {
  const nome = NOMES[conector] ?? conector
  const [sit, setSit] = useState<Situacao | null>(null)
  const [modal, setModal] = useState(false)
  const [est, setEst] = useState<Estimativa | null>(null)
  const [estErro, setEstErro] = useState('')
  const [erro, setErro] = useState('')
  const [toast, setToast] = useState('')
  const [enviando, setEnviando] = useState(false)
  const anterior = useRef<string | null>(null)
  const cancelarModal = useRef<HTMLButtonElement>(null)

  const carregar = useCallback(async () => {
    try {
      const s = await api<Situacao>(`/conectores/${conector}/completa`)
      setSit(s)
      return s
    } catch {
      return null
    }
  }, [conector])
  useEffect(() => { void carregar() }, [carregar])

  const status = sit?.execucao?.status ?? null
  const rodando = status === 'em_andamento'
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
      setEstErro(e instanceof ApiErro ? e.message : 'Não foi possível estimar as linhas da origem.')
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
  const pct = ex?.total_origem ? Math.min(100, Math.round((ex.processados / ex.total_origem) * 100)) : null

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

      {rodando && ex && (
        <div className="progresso-carga" role="status" aria-live="polite">
          <p><strong>Carregando…</strong> <span className="num">{milhar(ex.processados)}</span>
            {ex.total_origem != null && <> de <span className="num">{milhar(ex.total_origem)}</span></>} linhas
            {ex.tabela_atual && <span className="sutil"> · {ex.tabela_atual}</span>}</p>
          {pct != null && <progress max={100} value={pct} aria-label={`Progresso da carga completa de ${nome}`}>{pct}%</progress>}
          <button className="btn secundario" onClick={cancelar} disabled={ex.cancelamento_pedido}>
            {ex.cancelamento_pedido ? 'Cancelando…' : 'Cancelar carga'}
          </button>
        </div>
      )}

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
      {ex && !rodando && (ex.status === 'pausada' || ex.status === 'cancelada') && (
        <div className="aviso-ambar" role="status">
          <p>{ex.status === 'pausada' ? (ex.pausa_motivo ? `Carga pausada: ${ex.pausa_motivo}` : 'Carga pausada') : ex.mensagem}</p>
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
            <div><dt>Pendências</dt><dd>{milhar(sit.camadas.pendencias)}</dd></div>
          </dl>
          {sit.reconciliacao && (
            Object.values(sit.reconciliacao).every((r) => r.ok)
              ? <p><span className="selo pronto"><i aria-hidden="true" />Bronze bate com a origem</span></p>
              : <p className="aviso-ambar">Bronze diferente da origem em: {Object.entries(sit.reconciliacao).filter(([, r]) => !r.ok)
                .map(([t, r]) => `${t} (origem ${milhar(r.origem)}, bronze ${milhar(r.bronze)})`).join('; ')}</p>
          )}
        </div>
      )}

      <div aria-live="polite">{toast && <p className="aviso toast">{toast}</p>}</div>
    </div>
  )
}

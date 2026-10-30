import { useState } from 'react'
import { api, ApiErro } from './api'

export const SEM_SUPORTE_CANCELAR = 'Este servidor ainda não permite cancelar a execução pendente. A carga completa vai rodar no próximo ciclo da agenda.'

/** TL: item da agenda com forcar=true -> selo âmbar e "Cancelar execução pendente" (PUT /agenda/{conector} {forcar:false}). */
export default function ExecucaoPendente({ conector, nome, onMudou }: { conector: string; nome: string; onMudou: () => void }) {
  const [enviando, setEnviando] = useState(false)
  const [msg, setMsg] = useState('')
  const cancelar = async () => {
    setEnviando(true); setMsg('')
    try {
      await api(`/agenda/${encodeURIComponent(conector)}`, { method: 'PUT', body: JSON.stringify({ forcar: false }) })
      setMsg(`Execução pendente de ${nome} cancelada.`)
      onMudou()
    } catch (e) {
      setMsg(e instanceof ApiErro && (e.status === 404 || e.status === 422) ? SEM_SUPORTE_CANCELAR
        : e instanceof ApiErro ? e.message : 'Não foi possível cancelar a execução pendente. Tente de novo.')
    } finally {
      setEnviando(false)
    }
  }
  return (
    <div className="pendente">
      <span className="selo atencao"><i aria-hidden="true" />Execução pendente: carga completa</span>{' '}
      <button className="btn secundario" onClick={cancelar} disabled={enviando} aria-label={`Cancelar execução pendente de ${nome}`}>
        {enviando ? 'Cancelando…' : 'Cancelar execução pendente'}
      </button>
      {msg && <p className="sutil" role="status">{msg}</p>}
    </div>
  )
}

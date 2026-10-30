import { useState } from 'react'
import { api, ApiErro } from './api'

export const NAO_CANCELOU = 'Não foi possível cancelar a execução pendente. Ela continua agendada.'
export const SEM_SUPORTE_CANCELAR = NAO_CANCELOU  // 404/422 (API antiga): mesmo texto (UX final A1)

/** TL: item da agenda com forcar=true -> selo âmbar e "Cancelar execução pendente" (PUT /agenda/{conector} {forcar:false}). */
export default function ExecucaoPendente({ conector, nome, onMudou, pendente = true }: { conector: string; nome: string; onMudou: () => void; pendente?: boolean }) {
  const [enviando, setEnviando] = useState(false)
  const [msg, setMsg] = useState('')
  const cancelar = async () => {
    setEnviando(true); setMsg('')
    try {
      await api(`/agenda/${encodeURIComponent(conector)}`, { method: 'PUT', body: JSON.stringify({ forcar: false }) })
      // QA A1: API antiga devolve 200 e ignora forcar; só diz "cancelada" se a agenda relida confirmar forcar=false
      const ag = await api<{ itens?: { conector: string; forcar?: boolean }[] }>('/agenda')
      const item = Array.isArray(ag?.itens) ? ag.itens.find((i) => i.conector === conector) : undefined
      if (item && item.forcar === false) {
        setMsg('Execução pendente cancelada.')
        onMudou()
      } else {
        setMsg(NAO_CANCELOU)
      }
    } catch (e) {
      setMsg(e instanceof ApiErro && (e.status === 404 || e.status === 422) ? SEM_SUPORTE_CANCELAR
        : e instanceof ApiErro ? e.message : 'Não foi possível cancelar a execução pendente. Tente de novo.')
    } finally {
      setEnviando(false)
    }
  }
  if (!pendente && !msg) return null
  return (
    <div className="pendente">
      {pendente && <>
      <span className="selo atencao"><i aria-hidden="true" />Execução pendente: carga completa</span>{' '}
      <button className="btn secundario" onClick={cancelar} disabled={enviando} aria-label={`Cancelar execução pendente de ${nome}`}>
        {enviando ? 'Cancelando…' : 'Cancelar execução pendente'}
      </button>
      </>}
      {msg && <p className="sutil" role="status">{msg}</p>}
    </div>
  )
}

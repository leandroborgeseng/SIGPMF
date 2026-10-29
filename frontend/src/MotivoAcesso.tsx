import { type FormEvent, useEffect, useId, useState } from 'react'
import { MOTIVO_MIN, defineMotivo, limpaMotivo, motivoDefinido, motivoValido, ouveMotivo } from './motivo'

/** Motivo atual (re-renderiza quando muda). */
export function useMotivo(): string {
  const [m, setM] = useState(motivoDefinido())
  useEffect(() => ouveMotivo(() => setM(motivoDefinido())), [])
  return m
}

/** Formulário do motivo do acesso por função (admin_lgpd). Obrigatório, mínimo igual ao backend. */
export function MotivoAcesso({ area }: { area: string }) {
  const id = useId()
  const [texto, setTexto] = useState('')
  const [tentou, setTentou] = useState(false)
  const restante = Math.max(0, MOTIVO_MIN - texto.trim().length)
  const invalido = tentou && !motivoValido(texto)
  const enviar = (e: FormEvent) => {
    e.preventDefault()
    setTentou(true)
    if (motivoValido(texto)) defineMotivo(texto)
  }
  return (
    <section className="cartao" aria-labelledby={`${id}-t`}>
      <h2 id={`${id}-t`}>Motivo do acesso — {area}</h2>
      <p id={`${id}-d`}>
        Como administrador LGPD, você acessa esta área por função. Informe o motivo (mínimo de {MOTIVO_MIN} caracteres).
        Ele fica registrado na auditoria. Não inclua CPF, nomes ou outros dados pessoais.
      </p>
      <form onSubmit={enviar} noValidate>
        <label htmlFor={`${id}-m`}>Motivo <span aria-hidden="true">*</span></label>
        <textarea id={`${id}-m`} value={texto} onChange={(e) => setTexto(e.target.value)} required minLength={MOTIVO_MIN} maxLength={300}
          autoComplete="off" rows={3} aria-required="true" aria-invalid={invalido}
          aria-describedby={`${id}-d ${id}-c${invalido ? ` ${id}-e` : ''}`} />
        <p id={`${id}-c`} aria-live="polite">{restante > 0 ? `Faltam ${restante} caracteres.` : 'Tamanho mínimo atingido.'}</p>
        {invalido && <p id={`${id}-e`} role="alert" className="aviso erro">Informe um motivo com pelo menos {MOTIVO_MIN} caracteres.</p>}
        <button type="submit" className="btn">Continuar</button>
      </form>
    </section>
  )
}

/** Botão para trocar o motivo informado. */
export function TrocarMotivo() {
  return <button type="button" className="btn secundario" onClick={limpaMotivo}>Trocar motivo</button>
}

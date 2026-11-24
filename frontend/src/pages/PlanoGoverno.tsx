import { useEffect, useRef } from 'react'
import { mount } from '../plano-governo/app.js'
import '../plano-governo/style.css'

/** Usa o shell, a sessão, os vínculos e o banco do SIGPMF. */
export default function PlanoGoverno() {
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!root.current) return
    return mount(root.current)
  }, [])
  return <section aria-label="Plano de Governo"><div ref={root} className="pg-module" /></section>
}

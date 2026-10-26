import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, salvaSessao, sessaoAtual, type Sessao } from './api'

type Ctx = { sessao: Sessao | null; entrar: (u: string, s: string) => Promise<void>; sair: () => void }
const AuthCtx = createContext<Ctx>({ sessao: null, entrar: async () => {}, sair: () => {} })

export function AuthProvider({ children }: { children: ReactNode }) {
  const [sessao, setSessao] = useState<Sessao | null>(sessaoAtual())
  useEffect(() => {
    const f = () => setSessao(null)
    window.addEventListener('sigpmf:sair', f)
    return () => window.removeEventListener('sigpmf:sair', f)
  }, [])
  const entrar = async (usuario: string, senha: string) => {
    const r = await api<{ access_token: string; perfil: string; nome: string }>('/auth/login', {
      method: 'POST', body: JSON.stringify({ usuario, senha }),
    })
    const s = { token: r.access_token, perfil: r.perfil, nome: r.nome }
    salvaSessao(s)
    setSessao(s)
  }
  const sair = () => { salvaSessao(null); setSessao(null) }
  return <AuthCtx.Provider value={{ sessao, entrar, sair }}>{children}</AuthCtx.Provider>
}

export const useAuth = () => useContext(AuthCtx)

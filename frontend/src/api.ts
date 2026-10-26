export class ApiErro extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

const CHAVE = 'sigpmf_sessao'
export type Sessao = { token: string; perfil: string; nome: string }

export function sessaoAtual(): Sessao | null {
  try {
    return JSON.parse(sessionStorage.getItem(CHAVE) ?? 'null')
  } catch {
    return null
  }
}
export function salvaSessao(s: Sessao | null) {
  if (s) sessionStorage.setItem(CHAVE, JSON.stringify(s))
  else sessionStorage.removeItem(CHAVE)
}

export async function api<T>(caminho: string, opcoes: RequestInit = {}): Promise<T> {
  const s = sessaoAtual()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (s) headers.Authorization = `Bearer ${s.token}`
  const r = await fetch(`/api${caminho}`, { ...opcoes, headers: { ...headers, ...(opcoes.headers as object) } })
  if (r.status === 401 && caminho !== '/auth/login') {
    salvaSessao(null)
    window.dispatchEvent(new Event('sigpmf:sair'))
  }
  if (!r.ok) {
    let msg = 'Erro inesperado.'
    try {
      const d = await r.json()
      if (typeof d.detail === 'string') msg = d.detail
    } catch {
      /* corpo sem JSON */
    }
    throw new ApiErro(r.status, msg)
  }
  return r.json() as Promise<T>
}

const TZ = 'America/Sao_Paulo'
export function dataBR(v?: string | null): string {
  if (!v) return '—'
  return new Intl.DateTimeFormat('pt-BR', { timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(v))
}
export function dataHoraBR(v?: string | null): string {
  if (!v) return '—'
  const d = new Date(v)
  const f = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('pt-BR', { timeZone: TZ, ...o }).format(d)
  return `${f({ day: '2-digit', month: '2-digit', year: 'numeric' })} ${f({ hour: '2-digit', minute: '2-digit', hour12: false })}`
}

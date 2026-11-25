import { cabecalhoMotivo, limpaMotivo } from './motivo'
export class ApiErro extends Error {
  /** `corpo` é o JSON da resposta (detail string, objeto ou code). A mensagem continua o texto, quando houver. */
  constructor(public status: number, message: string, public corpo: unknown = null) {
    super(message)
  }
}

/** Texto legível do erro. detail string entra direto; objeto só contribui se tiver message/msg/detail em texto. */
function textoDetalhe(corpo: unknown): string | null {
  if (!corpo || typeof corpo !== 'object') return null
  const detail = (corpo as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const o = detail as Record<string, unknown>
    for (const k of ['message', 'msg', 'detail'] as const) {
      if (typeof o[k] === 'string') return o[k]
    }
  }
  return null
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
  else { sessionStorage.removeItem(CHAVE); limpaMotivo() }
}

export async function api<T>(caminho: string, opcoes: RequestInit = {}): Promise<T> {
  const s = sessaoAtual()
  const form = typeof FormData !== 'undefined' && opcoes.body instanceof FormData
  const headers: Record<string, string> = {}
  if (!form) headers['Content-Type'] = 'application/json'
  if (s) headers.Authorization = `Bearer ${s.token}`
  Object.assign(headers, cabecalhoMotivo(caminho, s?.perfil))
  const r = await fetch(`/api${caminho}`, { ...opcoes, headers: { ...headers, ...(opcoes.headers as object) } })
  if (r.status === 401 && caminho !== '/auth/login') {
    salvaSessao(null)
    window.dispatchEvent(new Event('sigpmf:sair'))
  }
  if (!r.ok) {
    let msg = 'Erro inesperado.'
    let corpo: unknown = null
    try {
      corpo = await r.json()
      const texto = textoDetalhe(corpo)
      if (texto) msg = texto
    } catch {
      /* corpo sem JSON */
    }
    throw new ApiErro(r.status, msg, corpo)
  }
  return r.json() as Promise<T>
}

const TZ = 'America/Sao_Paulo'
/** Converte texto de data da origem em Date sem lançar: aceita ISO, "AAAA-MM-DD HH:MM:SS" e DD/MM/AAAA. */
export function parseData(v?: string | null): Date | null {
  if (!v || typeof v !== 'string') return null
  const s = v.trim()
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s)
  if (m) return valida(new Date(`${m[1]}-${m[2]}-${m[3]}T12:00:00`))
  m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})/.exec(s)
  if (m) return valida(new Date(`${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}T12:00:00`))
  // Data-hora sem fuso (origem SQL Server) = horário de Brasília; com Z/offset, respeita o fuso informado.
  m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/.exec(s)
  if (m) return valida(new Date(`${m[1]}T${m[2]}-03:00`))
  return valida(new Date(s))
}
function valida(d: Date): Date | null { return Number.isNaN(d.getTime()) ? null : d }

/** Nunca lança: vazio → '—'; inválido → texto original. */
export function dataBR(v?: string | null): string {
  if (!v) return '—'
  const d = parseData(v)
  if (!d) return String(v)
  return new Intl.DateTimeFormat('pt-BR', { timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric' }).format(d)
}
export function dataHoraBR(v?: string | null): string {
  if (!v) return '—'
  const d = parseData(v)
  if (!d) return String(v)
  const f = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('pt-BR', { timeZone: TZ, ...o }).format(d)
  return `${f({ day: '2-digit', month: '2-digit', year: 'numeric' })} ${f({ hour: '2-digit', minute: '2-digit', hour12: false })}`
}

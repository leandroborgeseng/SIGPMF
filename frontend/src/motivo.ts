/**
 * Motivo do acesso por função do admin_lgpd (PR-06/#50). O backend exige o cabeçalho X-SIGPMF-Motivo
 * (mínimo MOTIVO_MIN caracteres) nas rotas abaixo quando SIGPMF_RBAC_SECRETARIA está ligada.
 * O motivo fica só em memória (nunca em URL, query string, localStorage ou log) e some ao sair.
 */
export const HEADER_MOTIVO = 'X-SIGPMF-Motivo'
export const MOTIVO_MIN = 10 // igual a MOTIVO_MIN do backend (app/services/acesso_secretaria.py)
export const PERFIL_LGPD = 'admin_lgpd'

/** Rotas (sem o prefixo /api) que exigem motivo para o admin_lgpd — espelho de POR_FUNCAO_COM_MOTIVO no backend. */
export const ROTAS_COM_MOTIVO: RegExp[] = [
  /^\/silver\/pendencias(\?|$)/,
  /^\/silver\/inconsistencias(\?|$)/,
  /^\/silver\/pendencias\/[^/?]+\/confirmar-obito$/,
  /^\/contagens\/[^/?]+\/cpfs-repetidos(\?|$)/,
  /^\/explorar\/(conectores|tabelas|perfil|amostra|funil|linhagem|revelar)$/,
]

let motivoAtual = ''
const ouvintes = new Set<() => void>()

export const motivoValido = (m: string) => m.trim().length >= MOTIVO_MIN
export const motivoDefinido = () => motivoAtual
export function defineMotivo(m: string) {
  motivoAtual = motivoValido(m) ? m.trim().slice(0, 300) : ''
  ouvintes.forEach((f) => f())
}
export const limpaMotivo = () => defineMotivo('')
export function ouveMotivo(f: () => void) { ouvintes.add(f); return () => { ouvintes.delete(f) } }
export const exigeMotivo = (caminho: string) => ROTAS_COM_MOTIVO.some((r) => r.test(caminho))

/** Cabeçalho a enviar (vazio se não se aplica). Só cabeçalho HTTP: nunca vai para a URL. */
export function cabecalhoMotivo(caminho: string, perfil: string | undefined): Record<string, string> {
  if (perfil !== PERFIL_LGPD || !motivoAtual || !exigeMotivo(caminho)) return {}
  return { [HEADER_MOTIVO]: paraLatin1(motivoAtual) }
}

/** Cabeçalho HTTP só aceita ISO-8859-1 (o backend lê como latin-1): acentos do pt-BR passam; o resto vira '?'. */
export const paraLatin1 = (m: string) => Array.from(m.replace(/[\r\n]+/g, ' ')).map((c) => (c.charCodeAt(0) <= 0xff && c.length === 1 ? c : '?')).join('')

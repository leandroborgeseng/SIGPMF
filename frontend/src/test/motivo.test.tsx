import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { api, salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { HEADER_MOTIVO, MOTIVO_MIN, cabecalhoMotivo, defineMotivo, exigeMotivo, limpaMotivo, motivoDefinido, paraLatin1 } from '../motivo'

const MOTIVO = 'ZZTESTE revisão de pendências'
function mockFetch() {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    void url; void init
    return new Response(JSON.stringify({ conectores: [], pode_revelar: false }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}
const hdr = (init?: RequestInit) => (init?.headers ?? {}) as Record<string, string>

beforeEach(() => { sessionStorage.clear(); limpaMotivo() })
afterEach(() => vi.unstubAllGlobals())

describe('motivo do admin_lgpd (X-SIGPMF-Motivo)', () => {
  it('mínimo igual ao backend (10) e rotas espelham POR_FUNCAO_COM_MOTIVO', () => {
    expect(MOTIVO_MIN).toBe(10)
    for (const r of ['/silver/pendencias', '/silver/pendencias?pagina=2', '/silver/inconsistencias', '/silver/pendencias/abc/confirmar-obito',
      '/contagens/7/cpfs-repetidos', '/explorar/amostra', '/explorar/revelar', '/explorar/conectores']) expect(exigeMotivo(r)).toBe(true)
    for (const r of ['/silver/cidadaos?busca=x', '/contagens/ultima', '/contagens/executar', '/dashboard', '/explorar/outra']) expect(exigeMotivo(r)).toBe(false)
  })
  it('motivo curto não é aceito; só admin_lgpd e só nas rotas exigidas recebem o cabeçalho', () => {
    defineMotivo('curto')
    expect(motivoDefinido()).toBe('')
    defineMotivo(MOTIVO)
    expect(cabecalhoMotivo('/contagens/1/cpfs-repetidos', 'admin_lgpd')).toEqual({ [HEADER_MOTIVO]: MOTIVO })
    expect(cabecalhoMotivo('/contagens/1/cpfs-repetidos', 'admin')).toEqual({})
    expect(cabecalhoMotivo('/dashboard', 'admin_lgpd')).toEqual({})
  })
  it('api() envia o motivo só no cabeçalho, nunca na URL; sair limpa o motivo', async () => {
    const f = mockFetch()
    salvaSessao({ token: 't', perfil: 'admin_lgpd', nome: 'T' })
    defineMotivo(MOTIVO)
    await api('/contagens/1/cpfs-repetidos')
    const [url, init] = f.mock.calls[0]
    expect(hdr(init)[HEADER_MOTIVO]).toBe(MOTIVO)
    expect(String(url)).not.toContain('ZZTESTE')
    expect(String(url)).not.toMatch(/motivo/i)
    await api('/dashboard')
    expect(hdr(f.mock.calls[1][1])[HEADER_MOTIVO]).toBeUndefined()
    salvaSessao(null)
    expect(motivoDefinido()).toBe('')
  })
  it('cabeçalho só com ISO-8859-1: acentos ficam, demais caracteres e quebras de linha não', () => {
    expect(paraLatin1('Revisão — ok\n😀')).toBe('Revisão ? ok ?')
  })
  it('explorador pede o motivo (acessível) antes de consultar e depois envia o cabeçalho', async () => {
    const f = mockFetch()
    salvaSessao({ token: 't', perfil: 'admin_lgpd', nome: 'T' })
    render(<MemoryRouter initialEntries={['/explorar']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
    const campo = await screen.findByRole('textbox', { name: /^Motivo/ })
    expect(campo).toHaveAttribute('aria-required', 'true')
    expect(f.mock.calls.filter(([u]) => String(u).includes('/explorar/'))).toHaveLength(0)
    const u = userEvent.setup()
    await u.type(campo, 'curto')
    await u.click(screen.getByRole('button', { name: 'Continuar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('pelo menos 10 caracteres')
    await u.clear(campo)
    await u.type(campo, MOTIVO)
    await u.click(screen.getByRole('button', { name: 'Continuar' }))
    await waitFor(() => expect(f.mock.calls.some(([u2]) => String(u2).includes('/explorar/conectores'))).toBe(true))
    const ch = f.mock.calls.find(([u2]) => String(u2).includes('/explorar/conectores'))!
    expect(hdr(ch[1])[HEADER_MOTIVO]).toBe(MOTIVO)
    expect(screen.getByRole('button', { name: 'Trocar motivo' })).toBeInTheDocument()
  })
  it('admin (vinculado) não vê o formulário de motivo', async () => {
    mockFetch()
    salvaSessao({ token: 't', perfil: 'admin', nome: 'T' })
    render(<MemoryRouter initialEntries={['/explorar']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Continuar' })).toBeNull())
  })
})

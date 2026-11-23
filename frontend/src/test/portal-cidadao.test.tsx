import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import PortalCidadao from '../pages/PortalCidadao'
import { AuthProvider } from '../auth'
let pedidos: { path: string; options?: RequestInit }[]
beforeEach(() => {
  pedidos = []; vi.stubGlobal('scrollTo', vi.fn()); HTMLElement.prototype.scrollIntoView = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    pedidos.push({ path, options })
    if (path.endsWith('/config')) return { ok: true, json: async () => ({ identificacao_disponivel: false, emissao_disponivel: false }) }
    if (path.endsWith('/identificar')) return { ok: true, json: async () => ({ desafio: 'desafio-sintetico', mensagem: 'Se os dados conferirem, enviaremos um código.' }) }
    return { ok: false, status: 401, json: async () => ({ detail: 'Entre novamente.' }) }
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
const montar = () => render(<MemoryRouter><PortalCidadao /></MemoryRouter>)
describe('Portal público do cidadão', () => {
  it('tem entrada própria com três dados e não busca ficha interna nem envia JWT de servidor', async () => {
    sessionStorage.setItem('sigpmf_sessao', JSON.stringify({ token: 'token-interno-nao-enviar' }))
    montar(); expect(await screen.findByLabelText('CPF')).toBeVisible(); expect(screen.getByLabelText('Data de nascimento')).toHaveAttribute('type', 'date')
    expect(screen.getByLabelText('Nome completo da mãe')).toBeVisible()
    await waitFor(() => expect(pedidos).toHaveLength(2))
    expect(pedidos.every(p => p.path.startsWith('/api/portal/'))).toBe(true)
    expect(JSON.stringify(pedidos)).not.toContain('token-interno-nao-enviar'); sessionStorage.clear()
  })
  it('conferência cadastral passa para código, sem revelar cadastro encontrado', async () => {
    montar(); fireEvent.change(screen.getByLabelText('CPF'), { target: { value: '52998224725' } }); fireEvent.change(screen.getByLabelText('Data de nascimento'), { target: { value: '1985-05-12' } }); fireEvent.change(screen.getByLabelText('Nome completo da mãe'), { target: { value: 'Pessoa de Teste' } })
    fireEvent.submit(screen.getByLabelText('CPF').closest('form')!)
    expect(await screen.findByLabelText('Código de confirmação')).toBeVisible()
    expect(screen.queryByLabelText('CPF')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Meu cadastro' })).not.toBeInTheDocument()
    const req = pedidos.find(p => p.path.endsWith('/identificar'))!
    expect(req.options?.method).toBe('POST'); expect(req.path).not.toContain('52998224725')
  })
  it('demonstração permite navegar por IPTU e acordos sem emitir guia nem alterar situação', async () => {
    montar(); fireEvent.click(screen.getByRole('button', { name: /Conhecer o portal/ }))
    expect(await screen.findByRole('heading', { name: 'Olá, Pessoa.' })).toBeVisible()
    fireEvent.click(screen.getAllByRole('button', { name: /Consultar guia atualizada/ })[0])
    expect(await screen.findByRole('status')).toHaveTextContent('demonstração')
    expect(pedidos.some(p => p.path.endsWith('/guias'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: /^Meus acordos$/ }))
    expect(await screen.findByText(/Acordo de demonstração/)).toBeVisible()
  })
  it('admin usa acesso de teste sem enviar código; o público continua no fluxo normal', async () => {
    sessionStorage.setItem('sigpmf_sessao', JSON.stringify({ token: 'jwt-admin-teste', perfil: 'admin', nome: 'Administrador' }))
    render(<MemoryRouter><AuthProvider><PortalCidadao /></AuthProvider></MemoryRouter>)
    expect(screen.getByText('Acesso de teste · administrador SIGPMF')).toBeVisible()
    fireEvent.change(screen.getByLabelText('CPF'), { target: { value: '52998224725' } })
    fireEvent.change(screen.getByLabelText('Data de nascimento'), { target: { value: '1985-05-12' } })
    fireEvent.change(screen.getByLabelText('Nome completo da mãe'), { target: { value: 'Pessoa de Teste' } })
    fireEvent.submit(screen.getByLabelText('CPF').closest('form')!)
    await waitFor(() => expect(pedidos.some(p => p.path.endsWith('/acesso-teste'))).toBe(true))
    const req = pedidos.find(p => p.path.endsWith('/acesso-teste'))!
    expect(req.options?.headers).toMatchObject({ Authorization: 'Bearer jwt-admin-teste' })
    expect(pedidos.some(p => p.path.endsWith('/identificar'))).toBe(false)
    expect(pedidos.some(p => p.path.endsWith('/confirmar'))).toBe(false)
    sessionStorage.clear()
  })

})

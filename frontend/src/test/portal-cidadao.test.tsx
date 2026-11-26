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
  it('demonstração mostra a ficha fictícia sem criar sessão', async () => {
    montar()
    fireEvent.click(screen.getByRole('button', { name: /Conhecer o portal/ }))
    fireEvent.click(screen.getAllByRole('button', { name: /Minha ficha/ })[0])
    expect(await screen.findByRole('heading', { name: 'Cadastro' })).toBeVisible()
    expect(screen.getAllByText('Rua de Exemplo, 100 · Franca/SP').length).toBeGreaterThan(0)
    expect(screen.getByText(/Posse atual/)).toBeVisible()
    expect(screen.getByText(/IPTU — imóvel EXEMPLO-001/)).toBeVisible()
    expect(screen.getByText(/Em aberto/)).toBeVisible()
    expect(screen.getByText('Nenhuma vacina registrada para você.')).toBeVisible()
    expect(screen.getByText('Nenhuma matrícula na rede municipal para você.')).toBeVisible()
    expect(screen.getByText('Nenhuma sepultura vinculada a você.')).toBeVisible()
    expect(screen.getByText('Árvore genealógica indisponível.')).toBeVisible()
    expect(screen.getByText(/Cadastro e valores fictícios/)).toBeVisible()
    expect(screen.getByRole('button', { name: /Sair da demonstração/ })).toBeVisible()
    expect(pedidos.some((p) => p.options?.method === 'POST')).toBe(false)
    expect(pedidos.some((p) => p.path.includes('/me/'))).toBe(false)
    expect(pedidos.some((p) => p.path.includes('/gold/'))).toBe(false)
    expect(screen.queryByText(/Quem consultou/)).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /ficha/i })).not.toBeInTheDocument()
  })
  it('cartão de parente só tem nome e parentesco, e a base ausente continua na tela', async () => {
    const conta = { nome: 'Pessoa ZZTESTE', cpf: '***.***.***-**', nascimento: '1980-08-01', atualizado_em: null, cadastro: [], imoveis: [], lancamentos: [], totais: { em_aberto: 0, vencido: 0 }, aviso_valores: 'teste', csrf: 'c', expira_em: new Date(Date.now() + 15 * 60 * 1000).toISOString() }
    vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
      pedidos.push({ path, options })
      if (path.endsWith('/config')) return { ok: true, json: async () => ({ identificacao_disponivel: false, emissao_disponivel: false }) }
      if (path.endsWith('/me/cadastro')) return { ok: true, json: async () => ({ nome_mae: { valor: 'Mae ZZTESTE', fonte: 'ZZTESTE', atualizado_em: null }, nome_pai: { valor: null, fonte: null, atualizado_em: null }, sexo: { valor: 'F', fonte: 'ZZTESTE', atualizado_em: null }, cns: '*** 0011 1111 ****', enderecos: [{ valor: 'Rua de Teste ZZTESTE, 80', fonte: 'ZZTESTE', atualizado_em: null }] }) }
      if (path.endsWith('/me/tributario')) return { ok: true, json: async () => ({ totais: { imoveis: 1, em_aberto: 150, divida_ativa: 80, inscricoes_divida: 1, lancamentos: 1 }, imoveis: [{ codi: 'ZZTESTE801', inscricao: 'ZZ-801', complemento: 'Rua Ficticia ZZTESTE, 100', endereco: 'Rua Ficticia ZZTESTE, 100', ativo: true }], lancamentos: [{ exercicio: 2026, tributo: 'IPTU', parcela: 1, vencimento: '2026-01-10', valor: 150, situacao: 'vencido' }], dividas: [{ ano: 2024, saldo: 80, em_aberto: true, origem: 'Imóvel ZZ-801' }], empresas: [] }) }
      if (path.endsWith('/me/saude')) return { ok: true, json: async () => ({ totais: { atendimentos: 0, vacinas: 0 }, proximos: [], atendimentos: [], vacinas: [] }) }
      if (path.endsWith('/me/educacao')) return { ok: true, json: async () => ({ matriculas: [] }) }
      if (path.endsWith('/me/cemiterio')) return { ok: true, json: async () => ({ titular: [], autorizado: [], leilao: [], sepultamento: [] }) }
      if (path.endsWith('/me/genealogia')) return { ok: true, json: async () => ({ estado: 'ok', parentes: [{ nome: 'Parente ZZTESTE', parentesco: 'mãe', cpf: '52998224725', cidadao_id: 'zzteste-parente', indicadores: { imovel: true } }] }) }
      if (path.endsWith('/me/linha-do-tempo')) return { ok: true, json: async () => ({ eventos: [] }) }
      if (path.endsWith('/me/fontes')) return { ok: true, json: async () => ({ carregadas: { vacinas: true, consultas: true, agendamentos: true, imoveis: true, lancamentos: true, dividas: true, empresas: false, educacao: true, cemiterio: true } }) }
      if (path.endsWith('/me')) return { ok: true, json: async () => conta }
      return { ok: false, status: 401, json: async () => ({ detail: 'Entre novamente.' }) }
    }))
    montar()
    fireEvent.click((await screen.findAllByRole('button', { name: /Minha ficha/ }))[0])
    const cartao = await screen.findByTestId('parente')
    expect(cartao).toHaveTextContent('Parente ZZTESTE')
    expect(cartao).toHaveTextContent('mãe')
    expect(cartao.querySelector('a')).toBeNull()
    expect(cartao.textContent).not.toContain('52998224725')
    expect(cartao.textContent).not.toContain('zzteste-parente')
    expect(cartao.textContent?.toLowerCase()).not.toContain('imóv')
    expect(screen.getByText('Rua de Teste ZZTESTE, 80')).toBeVisible()
    expect(screen.getByText(/Posse atual/)).toBeVisible()
    expect(screen.getByText('Nenhuma vacina registrada para você.')).toBeVisible()
    expect(screen.getByText('Nenhuma matrícula na rede municipal para você.')).toBeVisible()
    expect(screen.getByText('Nenhuma sepultura vinculada a você.')).toBeVisible()
    expect(screen.getByTestId('area-empresas')).toHaveTextContent('Base não carregada')
    expect(screen.queryByText(/Quem consultou/)).not.toBeInTheDocument()
    const fichas = pedidos.filter((p) => p.path.includes('/me/'))
    expect(fichas.length).toBeGreaterThan(0)
    expect(fichas.every((p) => p.path.startsWith('/api/portal/me/') && !/zzteste|cidadao_id|52998224725/.test(p.path))).toBe(true)
  })

})

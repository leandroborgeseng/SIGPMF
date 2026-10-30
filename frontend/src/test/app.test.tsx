import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'

type Rota = (url: string, init?: RequestInit) => [number, unknown]
function mockFetch(rota: Rota) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const [status, corpo] = rota(url, init)
    return new Response(JSON.stringify(corpo), { status, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}
function montar(caminho: string, perfil?: string) {
  salvaSessao(perfil ? { token: 't', perfil, nome: `Teste ${perfil}` } : null)
  return render(<MemoryRouter initialEntries={[caminho]}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}
async function semViolacoesSerias(el: HTMLElement) {
  const r = await axe.run(el, { rules: { 'color-contrast': { enabled: false } } }) // jsdom não calcula cores
  expect(r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
}

const DASH_VAZIO = { sistemas: 0, bronze: { registros: 0 }, silver: { cidadaos: 0, pendentes: 0 }, ultima_execucao: null }
const EXEC2 = { id: 2, tipo: 'carga_demo_amostra', status: 'sem_novidades', iniciado_em: '2026-10-26T00:30:00Z', finalizado_em: '2026-10-26T00:30:01Z', lidos: 200, novos: 0, atualizados: 0, ignorados: 200, lotes: 2 }
const CPF_MASC = '***.456.789-**'

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('acesso', () => {
  it('sem login vai para o login (A5/WEB-01)', async () => {
    const f = mockFetch(() => [200, {}])
    montar('/dashboard')
    expect(await screen.findByRole('heading', { name: /Entrar/ })).toBeInTheDocument()
    expect(f).not.toHaveBeenCalled()
  })

  it('navegação com aria-current (UX-S1); admin ganha Explorar dados (EX6)', async () => {
    mockFetch(() => [200, DASH_VAZIO])
    montar('/dashboard', 'admin')
    const nav = await screen.findByRole('navigation', { name: 'Navegação principal' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((l) => l.textContent)).toEqual(['Visão geral', 'Saúde', 'Fazenda', 'Dashboard das cargas', 'Monitor de cargas',
      'Conectores', 'Andamento das cargas', 'Simulador do Cidadão', 'Explorar dados', 'Explorador de vínculos', 'Políticas de dados',
      'Acesso por secretaria'])  // PR-06: grupo TI só para admin
    expect(links[3]).toHaveAttribute('aria-current', 'page')
    expect(links[0]).not.toHaveAttribute('aria-current')  // /painel não fica ativo em outras rotas (end)
  })

  it('gaveta abre com ☰ e fecha com Esc devolvendo o foco (UX-S2)', async () => {
    mockFetch(() => [200, DASH_VAZIO])
    montar('/dashboard', 'leitura')
    const btn = await screen.findByRole('button', { name: 'Abrir menu' })
    await userEvent.click(btn)
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    await userEvent.keyboard('{Escape}')
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    expect(btn).toHaveFocus()
  })
})

describe('dashboard', () => {
  it('vazio com CTA só para admin (UX-S3)', async () => {
    mockFetch(() => [200, DASH_VAZIO])
    const { container, unmount } = montar('/dashboard', 'admin')
    expect(await screen.findByText(/Nenhuma fonte de dados cadastrada ainda/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Cadastrar primeiro conector' })).toBeInTheDocument()
    await semViolacoesSerias(container)
    unmount()
    montar('/dashboard', 'operador')
    expect(await screen.findByText('Peça a um administrador para cadastrar o primeiro conector.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Cadastrar primeiro conector' })).toBeNull()
  })

  it('4 cartões e última execução dd/mm/aaaa hh:mm (UX-S4)', async () => {
    mockFetch(() => [200, { sistemas: 2, bronze: { registros: 199 }, silver: { cidadaos: 107, pendentes: 2 }, ultima_execucao: EXEC2 }])
    montar('/dashboard', 'leitura')
    expect(await screen.findByText('Cidadãos identificados')).toBeInTheDocument()
    expect(screen.getByText('107')).toBeInTheDocument()
    expect(screen.getByText(/25\/10\/2026 21:30/)).toBeInTheDocument()
  })

  it('erro com Tentar novamente (UX-S5)', async () => {
    mockFetch(() => [500, { detail: 'x' }])
    montar('/dashboard', 'leitura')
    expect(await screen.findByText(/Não foi possível carregar os indicadores/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeInTheDocument()
  })
})

describe('conectores', () => {
  const resp = {
    conectores: [
      { id: 1, codigo: 'DEMO_SAUDE', nome: 'Saúde — Prontuário', status: 'Ativo', ultima_execucao: EXEC2, credencial: 'Configurada via variável de ambiente', amostra_limite: 100, tabelas: [] },
      { id: 2, codigo: 'DEMO_CEMITERIO', nome: 'Cemitério — Sepultamentos', status: 'Nunca executado', ultima_execucao: null, credencial: 'Não configurada', amostra_limite: 100, tabelas: [] },
    ],
    execucoes: [EXEC2],
  }
  it('lista, selo sem novidades e operador sem cadastrar (UX-S6/S8)', async () => {
    mockFetch(() => [200, resp])
    const { container } = montar('/conectores', 'operador')
    expect(await screen.findByText(/Saúde — Prontuário \(DEMO_SAUDE\)/)).toBeInTheDocument()
    expect(screen.getByText(/Cemitério — Sepultamentos \(DEMO_CEMITERIO\)/)).toBeInTheDocument()
    expect(screen.getByText('Sem novidades · 0 novos, 0 atualizados')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cadastrar conector' })).toBeNull()
    await semViolacoesSerias(container)
  })

  it('falha no teste de conexão mostra mensagem limpa (UX-S7)', async () => {
    const msg = 'Falha ao conectar em DEMO_CEMITERIO: servidor não respondeu. Verifique a configuração do ambiente.'
    mockFetch((url) => (url.includes('/testar') ? [200, { ok: false, erro: msg }] : [200, resp]))
    montar('/conectores', 'operador')
    const botoes = await screen.findAllByRole('button', { name: 'Testar conexão' })
    await userEvent.click(botoes[1])
    expect(await screen.findByText(msg)).toBeInTheDocument()
  })
})

describe('simulador', () => {
  const ficha = {
    cidadao_id: 'c1', nome: 'Bruno Exemplo Souza', cpf: CPF_MASC, sistemas: ['DEMO_CEMITERIO', 'DEMO_SAUDE'], dominios: [],
    dados: [{ dominio: 'endereco', campo: 'endereco', valor: 'Rua Teste 2', fonte: 'DEMO_SAUDE', atualizado_em: '2026-03-05T12:00:00Z' }],
    enderecos: [{ valor: 'Rua Teste 2', fonte: 'DEMO_SAUDE', atualizado_em: '2026-03-05T12:00:00Z', mais_recente: true }, { valor: 'Rua Teste 1', fonte: 'DEMO_CEMITERIO', atualizado_em: '2025-01-10T12:00:00Z', mais_recente: false }],
  }
  const rota: Rota = (url, init) => {
    if (url.includes('/silver/cidadaos')) return [200, [{ cidadao_id: 'c1', nome: 'Bruno Exemplo Souza', cpf: CPF_MASC, sistemas: ['DEMO_SAUDE'] }]]
    if (url.includes('/revelar-cpf')) {
      const m = JSON.parse(String(init?.body ?? '{}')).motivo ?? ''
      return m.length >= 10 && !m.includes('sem-motivo-no-servidor') ? [200, { cpf: '12345678900', remascarar_em_segundos: 60 }] : [400, { detail: 'Informe o motivo para revelar o CPF.' }]
    }
    return [200, ficha]
  }
  async function abrir(perfil: string) {
    mockFetch(rota)
    const r = montar('/simulador', perfil)
    expect(await screen.findByText('Busque um cidadão por nome ou CPF para ver os dados integrados.')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Nome ou CPF completo'), 'Bruno')
    await userEvent.click(screen.getByRole('button', { name: 'Buscar' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Bruno Exemplo Souza' }))
    await screen.findByRole('heading', { name: 'Bruno Exemplo Souza' })
    return r
  }

  it('fonte e data dd/mm/aaaa e CPF mascarado (UX-S9/S10)', async () => {
    const { container } = await abrir('leitura')
    expect(screen.getByText(/Rua Teste 2 — Fonte: DEMO_SAUDE · Atualizado em 05\/03\/2026/)).toBeInTheDocument()
    expect(screen.getByText('Há outras versões em outras fontes')).toBeInTheDocument()
    expect(screen.getByTestId('cpf')).toHaveTextContent(CPF_MASC)
    expect(screen.queryByRole('button', { name: 'Revelar CPF' })).toBeNull()
    await semViolacoesSerias(container)
  })

  it('admin não vê o botão Revelar (UX-S11)', async () => {
    await abrir('admin')
    expect(screen.queryByRole('button', { name: 'Revelar CPF' })).toBeNull()
  })

  it('admin_lgpd revela com motivo, aviso e remascara em 60 s (UX-S11/S12/S13)', async () => {
    await abrir('admin_lgpd')
    const btn = screen.getByRole('button', { name: 'Revelar CPF' })
    await userEvent.click(btn)
    const dlg = screen.getByRole('dialog', { name: 'Revelar CPF' })
    expect(dlg).toHaveAttribute('aria-modal', 'true')
    const revelar = within(dlg).getByRole('button', { name: 'Revelar' })
    expect(revelar).toBeDisabled()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(btn).toHaveFocus()
    await userEvent.click(btn)
    await userEvent.type(screen.getByLabelText('Motivo'), 'Solicitação formal do titular')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await userEvent.click(screen.getByRole('button', { name: 'Revelar' }))
    expect(await screen.findByText('Revelação registrada na auditoria.')).toBeInTheDocument()
    expect(screen.getByTestId('cpf')).toHaveTextContent('123.456.789-00')
    vi.advanceTimersByTime(60_000)
    await waitFor(() => expect(screen.getByTestId('cpf')).toHaveTextContent(CPF_MASC))
    vi.useRealTimers()
  })

  it('400 da API vira a mensagem de motivo (UX-S11)', async () => {
    await abrir('admin_lgpd')
    await userEvent.click(screen.getByRole('button', { name: 'Revelar CPF' }))
    await userEvent.type(screen.getByLabelText('Motivo'), 'sem-motivo-no-servidor')
    await userEvent.click(screen.getByRole('button', { name: 'Revelar' }))
    expect(await screen.findByText('Informe o motivo para revelar o CPF.')).toBeInTheDocument()
  })
})

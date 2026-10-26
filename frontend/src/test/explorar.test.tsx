import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'

// Somente dados sintéticos, já mascarados como o backend devolve.
const CPF = '52998224725'
const EST = { processando: false, etapa: null, mensagem: null, ultima_carga: '2026-10-26T12:00:00Z' }
const linhas = (pag: number) => Array.from({ length: pag < 3 ? 50 : 20 }, (_, i) => ({
  id: (pag - 1) * 50 + i + 1, id_origem: `P${(pag - 1) * 50 + i}`, extraido_em: null, cpf: '***.982.247-**', nome: 'F*** S***', email: 'f***@exemplo.test',
}))
type Chamada = { url: string; corpo: Record<string, unknown> }

function mock(over: Partial<Record<string, (c: Record<string, unknown>) => [number, unknown]>> = {}, revelar = false) {
  const chamadas: Chamada[] = []
  const padrao: Record<string, (c: Record<string, unknown>) => [number, unknown]> = {
    conectores: () => [200, { conectores: [{ conector: 'EXSINT', estado: EST }], pode_revelar: revelar }],
    funil: () => [200, { bronze: 120, bronze_unicos: 120, quarentena: 0, prata: 1, prata_cidadaos: 1, ouro: 1, estado: EST, quarentena_disponivel: false,
      descartes: [{ etapa: 'bronze→prata', motivo: 'sem_correspondencia', quantidade: 1 }] }],
    tabelas: (c) => c.camada === 'ouro'
      ? [200, { camada: 'ouro', tabelas: [{ tabela: 'cidadao_dados', linhas: 0, ultima_carga: null }], total: 0, estado: { ...EST, processando: true, etapa: 'prata', mensagem: 'Processando… etapa prata' }, vazia: true, mensagem_vazia: 'Camada ainda não processada' }]
      : [200, { camada: c.camada, tabelas: [{ tabela: 'Pacientes', linhas: 120, ultima_carga: '2026-10-26T12:00:00Z' }], total: 120, estado: EST, vazia: false, mensagem_vazia: null }],
    perfil: () => [200, { tabela: 'Pacientes', linhas: 120, colunas: [
      { coluna: 'cpf', tipo: 'json:string', preenchido_pct: 0.8, nulo_pct: 99.2, distintos: 1, exemplos: ['***.982.247-**'], sensivel: true },
      { coluna: 'email', tipo: 'json:string', preenchido_pct: 100, nulo_pct: 0, distintos: 1, exemplos: ['f***@exemplo.test'], sensivel: true }] }],
    amostra: (c) => { const p = Number(c.pagina); return [200, { tabela: 'Pacientes', colunas: ['id', 'id_origem', 'cpf', 'nome', 'email'], id_coluna: 'id', cidadao_coluna: null,
      linhas: linhas(p), total: 120, pagina: p, por_pagina: 50, tem_anterior: p > 1, tem_proxima: p < 3 }] },
    revelar: () => [200, { valor: CPF, remascarar_em_segundos: 60 }],
  }
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const rota = url.replace('/api/explorar/', '')
    const corpo = init?.body ? JSON.parse(String(init.body)) : {}
    chamadas.push({ url, corpo })
    expect(init?.method).toBe('POST')
    const [s, b] = (over[rota] ?? padrao[rota] ?? (() => [404, {}]))(corpo)
    return new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return chamadas
}
const montar = (perfil: string) => {
  salvaSessao({ token: 't', perfil, nome: 'T' })
  return render(<MemoryRouter initialEntries={['/explorar']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

beforeEach(() => sessionStorage.clear())
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('Explorar dados', () => {
  it('EX6: operador não vê o menu nem a tela e não chama a API', async () => {
    const ch = mock()
    montar('operador')
    expect(screen.queryByRole('link', { name: 'Explorar dados' })).toBeNull()
    expect(await screen.findByRole('alert')).toHaveTextContent('não tem acesso')
    expect(ch.filter((c) => c.url.includes('/explorar/'))).toHaveLength(0)
  })

  it('EX6: 403 do servidor vira aviso de sem acesso', async () => {
    mock({ conectores: () => [403, { detail: 'x' }] })
    montar('admin')
    expect(await screen.findByText('Seu perfil não tem acesso ao explorador de dados.')).toBeInTheDocument()
  })

  it('EX1/EX4: admin vê menu, funil, tabelas e perfil mascarado', async () => {
    mock()
    montar('admin')
    expect(screen.getAllByRole('link', { name: /Explorar/ }).length).toBeGreaterThan(0)
    expect(await screen.findByText('sem_correspondencia')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Pacientes' })).toBeInTheDocument()
    expect((await screen.findAllByText('f***@exemplo.test')).length).toBeGreaterThan(0)
    expect(document.body.textContent).not.toContain(CPF)
    expect(screen.queryByRole('button', { name: /Revelar/ })).toBeNull() // admin comum não revela
    expect(screen.queryByText(/csv|exportar/i)).toBeNull()
  })

  it('EX2: paginação no servidor com Anterior/Próxima e filtro no corpo do POST', async () => {
    const ch = mock()
    montar('admin')
    const u = userEvent.setup()
    await screen.findByText('P0')
    expect(screen.getByRole('button', { name: 'Anterior' })).toBeDisabled()
    await u.click(screen.getByRole('button', { name: 'Próxima' }))
    await screen.findByText('P50')
    await u.click(screen.getByRole('button', { name: 'Próxima' }))
    await screen.findByText('P100')
    expect(screen.getByRole('button', { name: 'Próxima' })).toBeDisabled()
    const form = screen.getByRole('form', { name: 'Filtros' })
    await u.type(within(form).getByLabelText('cpf'), CPF)
    await u.click(within(form).getByRole('button', { name: 'Filtrar' }))
    await waitFor(() => expect(ch.some((c) => (c.corpo.filtros as Record<string, string>)?.cpf === CPF)).toBe(true))
    for (const c of ch) expect(c.url).not.toContain(CPF)
    expect(ch.every((c) => !c.url.includes('?'))).toBe(true)
  })

  it('EX2: admin_lgpd revela com motivo e volta a mascarar em 60 s', async () => {
    const ch = mock({}, true)
    montar('admin_lgpd')
    const u = userEvent.setup()
    const botoes = await screen.findAllByRole('button', { name: 'Revelar cpf' })
    await u.click(botoes[0])
    const dlg = screen.getByRole('dialog')
    const ok = within(dlg).getByRole('button', { name: 'Revelar' })
    await u.type(within(dlg).getByLabelText('Motivo'), 'curto')
    expect(ok).toBeDisabled()
    await u.type(within(dlg).getByLabelText('Motivo'), ' demais chamado 1')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await u.click(ok)
    expect(await screen.findByText(CPF)).toBeInTheDocument()
    expect(ch.find((c) => c.url.endsWith('/revelar'))?.corpo.motivo).toBe('curto demais chamado 1')
    vi.advanceTimersByTime(61_000)
    await waitFor(() => expect(screen.queryByText(CPF)).toBeNull())
  })

  it('EX7: camada vazia e processando', async () => {
    mock()
    montar('admin')
    const u = userEvent.setup()
    await u.click(await screen.findByRole('tab', { name: 'Ouro' }))
    expect(await screen.findByText('Camada ainda não processada')).toBeInTheDocument()
    expect(screen.getByText('Processando… etapa prata')).toBeInTheDocument()
  })
})

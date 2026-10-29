import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import AcessoSecretaria from '../pages/AcessoSecretaria'

const usuario = { id: 7, nome: 'ZZTESTE Maria', email: 'zzteste.maria@aion.local', perfil: 'leitura', secretarias: 2 }
const vinculos = [
  { id: 1, secretaria: 'SAUDE', secretaria_nome: 'Secretaria de Saúde', prefeito: false, inicio: '2026-01-01', fim: null, vencido: false, futuro: false, cadastrado_por: 'ZZTESTE Admin', cadastrado_em: '2026-10-29T12:00:00Z', versao: 1 },
  { id: 2, secretaria: 'FAZENDA', secretaria_nome: 'Secretaria da Fazenda', prefeito: false, inicio: '2026-01-01', fim: '2026-02-01', vencido: true, futuro: false, cadastrado_por: 'ZZTESTE Admin', cadastrado_em: '2026-01-01T12:00:00Z', versao: 3 },
]
const historico = [{ quando: '2026-10-29T12:00:00Z', quem: 'ZZTESTE Admin', acao: 'Adicionou', secretaria: 'Secretaria de Saúde', motivo: null }]

type Resp = { status?: number; corpo: unknown }
function mock(sobrescreve: Record<string, Resp> = {}) {
  const f = vi.fn(async (u: string, init?: RequestInit) => {
    const caminho = u.replace('/api/ti/acesso-secretaria', '')
    const padrao: Record<string, Resp> = {
      '/usuarios/buscar': { corpo: [usuario] },
      '/vinculos/listar': { corpo: { usuario, vinculos } },
      '/vinculos/historico': { corpo: historico },
      '/secretarias': { corpo: [{ codigo: 'SAUDE', nome: 'Secretaria de Saúde' }, { codigo: 'EDUCACAO', nome: 'Secretaria de Educação' }] },
      '/vinculos/remover': { corpo: { id: 1, removido: true } },
      '/vinculos/adicionar': { status: 201, corpo: {} },
    }
    const r = sobrescreve[caminho] ?? padrao[caminho] ?? { corpo: {} }
    void init
    return new Response(JSON.stringify(r.corpo), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}
const tela = () => render(<AuthProvider><MemoryRouter><AcessoSecretaria /></MemoryRouter></AuthProvider>)
async function abreUsuario() {
  await userEvent.type(screen.getByLabelText('Buscar usuário por nome ou e-mail'), 'zzteste')
  await userEvent.click(await screen.findByRole('button', { name: /ZZTESTE Maria/ }, { timeout: 2000 }))
  return screen.findByRole('table')
}

beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'admin', nome: 'Admin' }) })
afterEach(() => vi.unstubAllGlobals())

describe('PR-06 tela de TI: acesso por secretaria', () => {
  it('UXV8/R6: quem não é ADMIN vê só a mensagem de permissão e nenhuma chamada sai', () => {
    salvaSessao({ token: 't', perfil: 'admin_lgpd', nome: 'L' })
    const f = mock()
    tela()
    expect(screen.getByRole('alert')).toHaveTextContent('Você não tem permissão para esta tela.')
    expect(screen.queryByLabelText('Buscar usuário por nome ou e-mail')).toBeNull()
    expect(f).not.toHaveBeenCalled()
  })

  it('UXV1: busca e id vão só no corpo de POST, nunca na URL', async () => {
    const f = mock()
    tela()
    await abreUsuario()
    expect(f.mock.calls.length).toBeGreaterThan(0)
    for (const [url, init] of f.mock.calls) {
      expect(url).not.toMatch(/zzteste|\?|\/7\b/)
      expect((init as RequestInit).method).toBe('POST')
    }
    expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body))).toEqual({ termo: 'zzteste' })
  })

  it('não busca com menos de 3 caracteres e mostra o vazio', async () => {
    const f = mock({ '/usuarios/buscar': { corpo: [] } })
    tela()
    await userEvent.type(screen.getByLabelText('Buscar usuário por nome ou e-mail'), 'zz')
    await new Promise((r) => setTimeout(r, 400))
    expect(f).not.toHaveBeenCalled()
    await userEvent.type(screen.getByLabelText('Buscar usuário por nome ou e-mail'), 'z')
    expect(await screen.findByText('Nenhum usuário encontrado para esse termo.', {}, { timeout: 2000 })).toBeVisible()
  })

  it('UXV3/UXV4: vigência e cadastrado por em toda linha; vencido escrito "Vencido"', async () => {
    mock()
    tela()
    const t = await abreUsuario()
    const linhas = within(t).getAllByRole('row').slice(1)
    expect(linhas).toHaveLength(2)
    for (const l of linhas) {
      expect(l).toHaveTextContent(/de \d{2}\/\d{2}\/\d{4}/)
      expect(l).toHaveTextContent(/ZZTESTE Admin · \d{2}\/\d{2}\/\d{4} \d{2}:\d{2} BRT/)
    }
    expect(linhas[0]).toHaveTextContent(/de 01\/01\/2026, sem data de fim/)
    expect(linhas[0]).not.toHaveTextContent('até sem fim')
    expect(linhas[1]).toHaveTextContent(/de 01\/01\/2026 até 01\/02\/2026/)
    expect(within(linhas[1]).getByText('Vencido')).toBeVisible()
  })

  it('UXV2/UXV7: remover abre modal com foco em Cancelar, exige motivo e prende o foco', async () => {
    const f = mock()
    tela()
    await abreUsuario()
    await userEvent.click(screen.getByRole('button', { name: 'Remover Secretaria de Saúde' }))
    const dlg = screen.getByRole('dialog')
    expect(dlg).toHaveAttribute('aria-modal', 'true')
    expect(within(dlg).getByRole('button', { name: 'Cancelar' })).toHaveFocus()
    expect(dlg).toHaveTextContent('Remover o acesso de ZZTESTE Maria à Secretaria de Saúde? Ela deixa de ver os dados dessa secretaria na hora.')
    const rem = within(dlg).getByRole('button', { name: 'Remover acesso' })
    expect(rem).toBeDisabled()
    await userEvent.type(within(dlg).getByLabelText('Motivo (obrigatório)'), 'curto')
    expect(rem).toBeDisabled()
    await userEvent.type(within(dlg).getByLabelText('Motivo (obrigatório)'), ' mas agora longo')
    expect(rem).toBeEnabled()
    await userEvent.tab()
    await userEvent.tab()
    await userEvent.tab()
    expect(dlg.contains(document.activeElement)).toBe(true)
    await userEvent.click(rem)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const corpo = f.mock.calls.find(([u]) => String(u).endsWith('/vinculos/remover'))![1] as RequestInit
    expect(JSON.parse(String(corpo.body))).toEqual({ vinculo_id: 1, versao: 1, motivo: 'curto mas agora longo' })
    expect(await screen.findByText('Acesso à Secretaria de Saúde removido.')).toBeVisible()
    // UXV5: histórico recarregado sem recarregar a tela
    expect(f.mock.calls.filter(([u]) => String(u).endsWith('/vinculos/historico')).length).toBe(2)
  })

  it('conflito mostra o texto fixo e erro genérico nunca mostra a mensagem do servidor', async () => {
    mock({ '/vinculos/remover': { status: 409, corpo: { detail: 'qualquer' } }, '/vinculos/adicionar': { status: 500, corpo: { detail: 'Traceback segredo' } } })
    tela()
    await abreUsuario()
    await userEvent.click(screen.getByRole('button', { name: 'Remover Secretaria de Saúde' }))
    await userEvent.type(screen.getByLabelText('Motivo (obrigatório)'), 'motivo bem longo')
    await userEvent.click(screen.getByRole('button', { name: 'Remover acesso' }))
    expect(await screen.findByText('Este vínculo foi alterado por outra pessoa. Recarregue para ver a versão atual.')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Cancelar' }))
    await userEvent.click(screen.getByRole('button', { name: 'Adicionar secretaria' }))
    await userEvent.selectOptions(screen.getByLabelText('Secretaria'), 'EDUCACAO')
    await userEvent.click(screen.getByRole('button', { name: 'Salvar' }))
    expect(await screen.findByText('Não foi possível salvar. Tente de novo.')).toBeVisible()
    expect(screen.queryByText(/Traceback/)).toBeNull()
    expect(screen.getByLabelText('Secretaria')).toHaveValue('EDUCACAO')
  })

  it('formulário: Salvar desabilitado sem secretaria e fim antes do início com erro escrito', async () => {
    mock()
    tela()
    await abreUsuario()
    await userEvent.click(screen.getByRole('button', { name: 'Adicionar secretaria' }))
    expect(screen.getByRole('button', { name: 'Salvar' })).toBeDisabled()
    const opcoes = within(screen.getByLabelText('Secretaria')).getAllByRole('option').map((o) => o.textContent)
    expect(opcoes).toEqual(['Escolha a secretaria', 'Secretaria de Educação'])  // sem as que já tem
    await userEvent.selectOptions(screen.getByLabelText('Secretaria'), 'EDUCACAO')
    const ini = screen.getByLabelText('Data de início') as HTMLInputElement
    await act(async () => { await userEvent.clear(ini); await userEvent.type(ini, '2026-10-10') })
    await userEvent.type(screen.getByLabelText('Data de fim (opcional)'), '2026-10-01')
    expect(screen.getByText('A data de fim não pode ser anterior à de início.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Salvar' })).toBeDisabled()
  })

  it('usuário sem secretaria mostra o vazio, e o prefeito não tem Remover', async () => {
    mock({ '/vinculos/listar': { corpo: { usuario, vinculos: [] } } })
    const { unmount } = tela()
    await userEvent.type(screen.getByLabelText('Buscar usuário por nome ou e-mail'), 'zzteste')
    await userEvent.click(await screen.findByRole('button', { name: /ZZTESTE Maria/ }, { timeout: 2000 }))
    expect(await screen.findByText('Este usuário ainda não tem acesso a nenhuma secretaria.')).toBeVisible()
    unmount()
    mock({ '/vinculos/listar': { corpo: { usuario, vinculos: [{ ...vinculos[0], id: 9, secretaria: 'TODAS', secretaria_nome: 'Todas as secretarias (prefeito)', prefeito: true }] } } })
    tela()
    await abreUsuario()
    expect(screen.getByText('Todas as secretarias (prefeito)')).toBeVisible()
    expect(screen.queryByRole('button', { name: /^Remover/ })).toBeNull()
  })

  it('histórico recolhido e sem violações de acessibilidade', async () => {
    mock()
    const { container } = tela()
    await abreUsuario()
    const det = screen.getByText('Ver histórico de alterações').closest('details')!
    expect(det.open).toBe(false)
    expect(det.querySelector('time')).toHaveTextContent(/ BRT$/)
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations).toEqual([])
  })
})

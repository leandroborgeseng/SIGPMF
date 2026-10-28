import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import Politicas from '../pages/Politicas'

const resp = {
  regras: [{ camada: 'Bronze', titulo: 'Só a versão atual', texto: 'x' }],
  conjuntos: [{ nome: 'Atendimentos, agenda e vacinação', origem: 'Saúde', classe: 'sensivel', base: 'art. 11', retencao: '12 meses', tratamento: 'Sem conteúdo clínico', quem: 'Todos' }],
  areas: ['Cadastro', 'Vínculos'], perfis: [{ perfil: 'leitura', celulas: ['ver', 'nada'] }],
  retencao: { bronze_versoes_dias: 7, logs_dias: 90, ultima: { status: 'sucesso', iniciado_em: '2026-10-28T05:32:00Z', mensagem: 'Retenção: 10 versão(ões)', apagados: 10 } },
}
const mock = () => { const f = vi.fn(async (_u: string, init?: RequestInit) => new Response(JSON.stringify(init?.method === 'POST' ? { status: 'sucesso', mensagem: 'Retenção: 3 apagados', apagados: 3 } : resp), { status: 200, headers: { 'Content-Type': 'application/json' } })); vi.stubGlobal('fetch', f); return f }
beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('políticas de dados (onda 7)', () => {
  it('mostra regras, conjunto sensível, matriz e limpeza; admin executa agora', async () => {
    salvaSessao({ token: 't', perfil: 'admin', nome: 'T' })
    const f = mock()
    const { container } = render(<AuthProvider><Politicas /></AuthProvider>)
    const tab = await screen.findByRole('table', { name: 'Conjuntos de dados e suas políticas' })
    expect(within(tab).getByText('Sensível')).toBeVisible()
    expect(within(screen.getByRole('table', { name: 'Acesso por perfil e área' })).getByText('Vê')).toBeVisible()
    expect(screen.getByText('Última execução ok')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Executar agora' }))
    expect(await screen.findByText('Retenção: 3 apagados')).toBeVisible()
    expect(f).toHaveBeenCalledWith('/api/politicas/retencao/executar', expect.objectContaining({ method: 'POST' }))
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })
  it('leitura não vê o botão', async () => {
    salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' })
    mock()
    render(<AuthProvider><Politicas /></AuthProvider>)
    await screen.findByText('Última execução ok')
    expect(screen.queryByRole('button', { name: 'Executar agora' })).toBeNull()
  })
})

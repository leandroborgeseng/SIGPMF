import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'

// PR-A (UXA1–UXA5, CT1–CT3, CT13, CT14). Dados mockados ZZTESTE, nada real.
const Z = { bronze: 0, prata: 0, ouro: 0, pendentes: 0 }
const SEM = 'Rode a amostra acima e aprove o resultado para liberar a carga completa.'
const PEND = 'Confira o resultado e clique em Aprovar amostra para liberar a carga completa.'
const base = (id: number, nome: string, conector: string, o: object = {}) => ({
  id, nome, descricao: 'ZZTESTE', grupo: 'painel_cidadao', triagem: 'schema', prioridade: id, status: 'implementado', conector_id: conector,
  tem_conector: true, pode_carregar: true, motivo_bloqueio: null, tem_amostra: false, com_erro: false, contagens: Z, ultima_carga: null,
  completa_disponivel: true, completa_liberada: false, completa_motivo: SEM, exige_amostra: true, amostra: null, amostra_limite: 1000, ...o,
})
const AM_OK = { id: 77, status: 'sucesso', finalizado_em: '2026-10-29T23:00:00Z', lidos: 1000, gravados: 997, com_erro: 3, motivo: null, aprovada: false }
const BASES = [
  base(1, 'Dívida ativa', 'divida'),
  base(2, 'Financeiro', 'financeiro'),
  base(3, 'API SIL', 'sil'),
  base(4, 'IPTU', 'iptu', { amostra: { ...AM_OK, id: 5, aprovada: true }, completa_liberada: true, completa_motivo: null }),
]
const SIT = (liberada: boolean, motivo: string | null) => ({ conector: 'x', liberada, motivo, execucao: null, camadas: { bronze: 0, prata: 0, pendencias: 0 }, reconciliacao: null })
const PROGRESSO_VAZIO = { agora: '2026-10-29T18:00:00Z', rodando: false, atualizar_a_cada_s: 30, fontes: [] }

function mock(rota: (url: string, init?: RequestInit) => [number, unknown] | null) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    let r: [number, unknown] | null = rota(url, init)
    if (!r) {
      if (url === '/api/fontes/progresso') r = [200, PROGRESSO_VAZIO]
      else if (url.startsWith('/api/agenda')) r = [200, { agendas: [] }]
      else if (/\/completa$/.test(url)) r = [200, SIT(false, SEM)]
      else if (url === '/api/conectores') r = [200, { conectores: [], execucoes: [], bases: BASES, totais: {}, carga_habilitada: true }]
      else r = [404, { detail: 'não mockado' }]
    }
    return new Response(JSON.stringify(r[1]), { status: r[0], headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}
const montar = () => {
  salvaSessao({ token: 't', perfil: 'admin', nome: 'T' })
  return render(<MemoryRouter initialEntries={['/conectores']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}
const cartao = async (nome: string) => (await screen.findByRole('heading', { name: new RegExp(`^${nome}`) })).closest('li') as HTMLElement

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('amostra obrigatória no card (PR-A)', () => {
  it('CT14/UXA5: nenhuma fonte que exige amostra fica sem jeito de rodá-la', async () => {
    mock(() => null)
    const { container } = montar()
    for (const b of BASES) {
      const c = await cartao(b.nome)
      const rodar = within(c).queryByRole('button', { name: `Rodar amostra (1.000 linhas) de ${b.nome}` })
      const aprovada = within(c).queryByText('Amostra aprovada')
      expect(rodar !== null || aprovada !== null).toBe(true)
      if (rodar) expect(rodar).toBeEnabled()
    }
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
  })

  it('UXA1/CT1/CT13: completa desabilitada com o motivo dizendo onde clicar', async () => {
    mock(() => null)
    montar()
    const c = await cartao('Dívida ativa')
    expect(within(c).getByRole('button', { name: 'Rodar amostra (1.000 linhas) de Dívida ativa' })).toHaveTextContent('Rodar amostra (1.000 linhas)')
    expect(within(c).getByRole('button', { name: 'Executar carga completa de Dívida ativa' })).toBeDisabled()
    expect(within(c).getByText(/Rode a amostra acima e aprove o resultado para liberar a carga completa\./)).toBeInTheDocument()
  })

  it('UXA2/UXA3/CT2: roda, mostra linhas e erros, "Ver erros", aprova e libera', async () => {
    let aprovada = false
    let rodou = false
    const f = mock((url, init) => {
      if (url === '/api/conectores/divida/amostra' && init?.method === 'POST') rodou = true
      if (url === '/api/conectores/divida/amostra' && init?.method === 'POST') return [202, { id: 77, status: 'em_andamento', em_andamento: true, lidos: 0, ignorados: 0, mensagem: null }]
      if (url === '/api/conectores/divida/amostra/77') return [200, { id: 77, status: 'sucesso', em_andamento: false, lidos: 1000, ignorados: 3, mensagem: null }]
      if (url === '/api/conectores/divida/completa/quarentena') return [200, { linhas: [{ id: 1, tabela: 'ZZTESTE_DIVIDA', motivo: 'chave nula', campos_nulos: ['id'], referencia: 'linha 12' }] }]
      if (url === '/api/conectores/divida/amostra/77/aprovar') { aprovada = true; return [200, { ...AM_OK, aprovada: true }] }
      if (url === '/api/conectores') return [200, { conectores: [], execucoes: [], carga_habilitada: true,
        bases: BASES.map((b) => b.conector_id !== 'divida' ? b
          : aprovada ? { ...b, amostra: { ...AM_OK, aprovada: true }, completa_liberada: true, completa_motivo: null }
          : rodou ? { ...b, amostra: AM_OK, completa_motivo: PEND } : b) }]
      if (url === '/api/conectores/divida/completa') return [200, SIT(aprovada, aprovada ? null : PEND)]
      return null
    })
    montar()
    const c = await cartao('Dívida ativa')
    await userEvent.click(within(c).getByRole('button', { name: 'Rodar amostra (1.000 linhas) de Dívida ativa' }))
    const ocupado = within(c).getByRole('button', { name: 'Rodando amostra de Dívida ativa…' })
    expect(ocupado).toBeDisabled()
    expect(ocupado).toHaveAttribute('aria-busy', 'true')
    await screen.findByText(/clique em Aprovar amostra para liberar a carga completa/, {}, { timeout: 5000 })
    await screen.findByText(/Amostra concluída: 1.000 linhas lidas, 3 com erro/, {}, { timeout: 5000 })
    expect(await screen.findByText(/Amostra concluída: 1.000 linhas lidas, 3 com erro/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Rodar de novo a amostra de Dívida ativa' }, { timeout: 5000 })).toBeEnabled()
    expect(screen.getByText(/clique em Aprovar amostra para liberar a carga completa/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Ver erros da amostra de Dívida ativa' }))
    expect(await screen.findByText(/chave nula/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Aprovar amostra de Dívida ativa' }))
    await waitFor(async () => expect(within(await cartao('Dívida ativa')).getByText('Amostra aprovada')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Executar carga completa de Dívida ativa' })).toBeEnabled())
    expect(f.mock.calls.some(([u]) => String(u).includes('/franca/imoveis/atualizar'))).toBe(false)
  })

  it('UXA4: amostra com falha mostra o motivo e o botão segue ativo', async () => {
    let falhou = false
    mock((url, init) => {
      if (url === '/api/conectores/sil/amostra' && init?.method === 'POST') { falhou = true; return [202, { id: 9, status: 'falha', em_andamento: false, lidos: 0, ignorados: 0, mensagem: null, erro: { motivo: 'Sem acesso à API (ZZTESTE)' } }] }
      if (url === '/api/conectores' && falhou) return [200, { conectores: [], execucoes: [], carga_habilitada: true,
        bases: BASES.map((b) => b.conector_id === 'sil' ? { ...b, amostra: { ...AM_OK, id: 9, status: 'falha', lidos: 0, com_erro: 0, motivo: 'Sem acesso à API (ZZTESTE)' } } : b) }]
      return null
    })
    montar()
    const c = await cartao('API SIL')
    await userEvent.click(within(c).getByRole('button', { name: 'Rodar amostra (1.000 linhas) de API SIL' }))
    await screen.findByText('A amostra falhou: Sem acesso à API (ZZTESTE). Corrija e rode de novo.')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Rodar amostra (1.000 linhas) de API SIL' })).toBeEnabled())
    expect(screen.getByText('A amostra falhou: Sem acesso à API (ZZTESTE). Corrija e rode de novo.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Aprovar amostra de API SIL/ })).toBeNull()
  })

  it('CT3: amostra que já passou aparece como aprovada, sem pedir outra', async () => {
    mock(() => null)
    montar()
    const c = await cartao('IPTU')
    expect(within(c).getByText('Amostra aprovada')).toBeInTheDocument()
    expect(within(c).queryByRole('button', { name: /Aprovar amostra/ })).toBeNull()
  })

  it('CT15/UXP9: card da base mostra o tipo real da última execução e o aviso do teto', async () => {
    const ult = { id: 9, tipo: 'enriquecimento_sil', tipo_texto: 'Amostra', status: 'sucesso', iniciado_em: '2026-10-30T00:20:00Z', finalizado_em: null,
      lidos: 49, mensagem: null, texto_limite: 'Amostra: 49 linhas (parou no limite de 1 lote). Não é o total da origem.' }
    mock((url) => url === '/api/conectores' ? [200, { conectores: [], execucoes: [], carga_habilitada: true,
      bases: BASES.map((b) => b.conector_id === 'sil' ? { ...b, ultima_carga: ult } : b) }] : null)
    montar()
    const c = await cartao('API SIL')
    expect(within(c).getByText(/Última carga: .* · Amostra · sucesso/)).toBeInTheDocument()
    expect(within(c).getByText('Amostra: 49 linhas (parou no limite de 1 lote). Não é o total da origem.')).toBeInTheDocument()
    expect(within(c).queryByText(/· Carga completa ·/)).toBeNull()
  })

  it('M1: selo segue o estado da amostra e o verde só aparece depois da aprovação', async () => {
    let falhou = false
    mock((url, init) => {
      if (url === '/api/conectores/sil/amostra' && init?.method === 'POST') { falhou = true; return [202, { id: 9, status: 'falha', em_andamento: false, lidos: 0, ignorados: 0, mensagem: null, erro: { motivo: 'ZZTESTE' } }] }
      if (url === '/api/conectores') return [200, { conectores: [], execucoes: [], carga_habilitada: true, bases: [
        ...BASES.map((b) => b.conector_id === 'sil' && falhou ? { ...b, amostra: { ...AM_OK, id: 9, status: 'falha', motivo: 'ZZTESTE' } } : b),
        base(5, 'Cemiterio', 'cemiterio', { amostra: AM_OK, completa_motivo: PEND })] }]
      return null
    })
    montar()
    const selo = (c: HTMLElement) => c.querySelector('.selo') as HTMLElement
    expect(selo(await cartao('Dívida ativa'))).toHaveTextContent(/^Aguardando amostra$/)
    expect(selo(await cartao('Cemiterio'))).toHaveTextContent(/^Amostra para aprovar$/)
    expect(selo(await cartao('IPTU'))).toHaveTextContent(/^Pronto para carga$/)
    expect(screen.queryByText(/^Pronto$/)).toBeNull()
    await userEvent.click(within(await cartao('API SIL')).getByRole('button', { name: /Rodar amostra .* de API SIL/ }))
    await waitFor(async () => expect(selo(await cartao('API SIL'))).toHaveTextContent(/^Amostra falhou$/))
  })

  it('M1: durante a amostra o selo é "Rodando amostra"', async () => {
    mock((url, init) => {
      if (url === '/api/conectores/divida/amostra' && init?.method === 'POST') return [202, { id: 77, status: 'em_andamento', em_andamento: true, lidos: 0, ignorados: 0, mensagem: null }]
      if (url === '/api/conectores/divida/amostra/77') return [200, { id: 77, status: 'em_andamento', em_andamento: true, lidos: 0, ignorados: 0, mensagem: null }]
      return null
    })
    montar()
    const c = await cartao('Dívida ativa')
    await userEvent.click(within(c).getByRole('button', { name: /Rodar amostra .* de Dívida ativa/ }))
    expect(c.querySelector('.selo')).toHaveTextContent(/^Rodando amostra$/)
  })

  it('M2: motivo abaixo do botão, em largura total, ligado por aria-describedby', async () => {
    mock(() => null)
    montar()
    const c = await cartao('Dívida ativa')
    const b = within(c).getByRole('button', { name: 'Executar carga completa de Dívida ativa' })
    const id = b.getAttribute('aria-describedby') as string
    const m = document.getElementById(id) as HTMLElement
    expect(m.tagName).toBe('P')
    expect(m).toHaveTextContent('Rode a amostra acima e aprove o resultado para liberar a carga completa.')
    expect(m.textContent).not.toMatch(/Conectores > Bases de origem/)
    expect(b.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('M3: aprovada, "Executar carga completa" é o primário e "Rodar de novo" é terciário', async () => {
    mock((url) => /iptu\/completa$/.test(url) ? [200, { conector: 'iptu', liberada: true, motivo: null, execucao: null, camadas: { bronze: 0, prata: 0, pendencias: 0 }, reconciliacao: null }] : null)
    montar()
    const c = await cartao('IPTU')
    const exec = await within(c).findByRole('button', { name: 'Executar carga completa de IPTU' })
    await waitFor(() => expect(exec).toBeEnabled())
    expect(exec.className).toBe('btn')
    const de = within(c).getByRole('button', { name: 'Rodar de novo a amostra de IPTU' })
    expect(de).toHaveClass('ghost')
    expect(de).not.toHaveClass('outlined')
  })
})

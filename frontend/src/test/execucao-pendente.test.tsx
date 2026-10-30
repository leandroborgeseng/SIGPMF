import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { NAO_CANCELOU, SEM_SUPORTE_CANCELAR } from '../ExecucaoPendente'
import { confirmaPendente } from '../PainelAmostra'

// PR-B (TL): execução pendente (forcar=true) no card do conector. Dados mockados ZZTESTE, nada real.
const Z = { bronze: 0, prata: 0, ouro: 0, pendentes: 0 }
const SEM = 'Rode a amostra acima e aprove o resultado para liberar a carga completa.'
const PEND = 'Confira o resultado e clique em Aprovar amostra para liberar a carga completa.'
const base = (id: number, nome: string, conector: string, o: object = {}) => ({
  id, nome, descricao: 'ZZTESTE', grupo: 'painel_cidadao', triagem: 'schema', prioridade: id, status: 'implementado', conector_id: conector,
  tem_conector: true, pode_carregar: true, motivo_bloqueio: null, tem_amostra: false, com_erro: false, contagens: Z, ultima_carga: null,
  completa_disponivel: true, completa_liberada: false, completa_motivo: SEM, exige_amostra: true, amostra: null, amostra_limite: 1000, ...o,
})
const AM_OK = { id: 77, status: 'sucesso', finalizado_em: '2026-10-29T23:00:00Z', lidos: 1000, gravados: 997, com_erro: 3, motivo: null, aprovada: false }
const BASES_ORIG = [
  base(1, 'Dívida ativa', 'divida'),
  base(2, 'Financeiro', 'financeiro'),
  base(3, 'API SIL', 'sil'),
  base(4, 'IPTU', 'iptu', { amostra: { ...AM_OK, id: 5, aprovada: true }, completa_liberada: true, completa_motivo: null }),
]
const BASES = [base(1, 'Dívida ativa', 'divida', { amostra: AM_OK, completa_motivo: PEND })]
void BASES_ORIG
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

const AGENDA = {
  habilitada: true, janela: '22:00-06:00', dia_todo: false, fuso: 'America/Sao_Paulo', pular_sem_mudanca: true, alerta_email: false,
  agora: '2026-10-29T23:00:00Z', historico: [],
  itens: [{ conector: 'divida', nome: 'Dívida ativa', horario: '02:00', ativo: true, ordem: 1, forcar: true, ultimo_status: null,
    ultimo_status_texto: null, ultima_mensagem: null, ultimo_inicio_em: null, ultimo_fim_em: null, tentativas: 0, proxima_em: null,
    horario_fora_da_janela: false, ultima_execucao: null, em_andamento: null }],
}

describe('execução pendente (forcar=true)', () => {
  it('A1: PUT 200 mas a agenda relida ainda tem forcar=true -> não diz "cancelada" e mantém o selo', async () => {
    mock((url, init) => {
      if (url === '/api/agenda/divida' && init?.method === 'PUT') return [200, { ok: true }]  // API antiga ignora forcar
      if (url === '/api/agenda') return [200, AGENDA]
      return null
    })
    montar()
    const c = await cartao('Dívida ativa')
    await userEvent.click(await within(c).findByRole('button', { name: 'Cancelar execução pendente de Dívida ativa' }))
    expect(await within(c).findByText(NAO_CANCELOU)).toBeVisible()
    expect(within(c).queryByText(/cancelada\./)).toBeNull()
    expect(within(c).getByText('Execução pendente: carga completa')).toBeVisible()
  })

  it('A1: com a agenda relida em forcar=false, diz "cancelada" e o selo some', async () => {
    let forcar = true
    mock((url, init) => {
      if (url === '/api/agenda/divida' && init?.method === 'PUT') { forcar = false; return [200, { ok: true }] }
      if (url === '/api/agenda') return [200, { ...AGENDA, itens: [{ ...AGENDA.itens[0], forcar }] }]
      return null
    })
    montar()
    const c = await cartao('Dívida ativa')
    await userEvent.click(await within(c).findByRole('button', { name: 'Cancelar execução pendente de Dívida ativa' }))
    await waitFor(() => expect(within(c).queryByText('Execução pendente: carga completa')).toBeNull())
    expect(within(c).getByText('Execução pendente cancelada.')).toBeVisible()
  })

  it('A1: durante o pedido o botão fica desabilitado com "Cancelando…"; depois "Execução pendente cancelada."', async () => {
    let libera: () => void = () => {}
    const espera = new Promise<void>((r) => { libera = r })
    let forcar = true
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/agenda/divida' && init?.method === 'PUT') { await espera; forcar = false; return new Response('{}', { status: 200 }) }
      if (url === '/api/agenda') return new Response(JSON.stringify({ ...AGENDA, itens: [{ ...AGENDA.itens[0], forcar }] }), { status: 200 })
      if (url === '/api/conectores') return new Response(JSON.stringify({ conectores: [], execucoes: [], bases: BASES, totais: {}, carga_habilitada: true }), { status: 200 })
      if (url === '/api/fontes/progresso') return new Response(JSON.stringify(PROGRESSO_VAZIO), { status: 200 })
      return new Response(JSON.stringify(SIT(false, SEM)), { status: 200 })
    })
    vi.stubGlobal('fetch', f)
    montar()
    const c = await cartao('Dívida ativa')
    await userEvent.click(await within(c).findByRole('button', { name: 'Cancelar execução pendente de Dívida ativa' }))
    const b = within(c).getByRole('button', { name: 'Cancelar execução pendente de Dívida ativa' })
    expect(b).toBeDisabled()
    expect(b).toHaveTextContent('Cancelando…')
    libera()
    await waitFor(() => expect(within(c).queryByText('Execução pendente: carga completa')).toBeNull())
    expect(within(c).getByText('Execução pendente cancelada.')).toBeVisible()
  })

  it('M3: âncora fonte-<id> nos cards fixos de Conectores (SIL, Educação) sem duplicar a da base', async () => {
    mock((url) => url === '/api/conectores'
      ? [200, { conectores: [{ id: 9, codigo: 'SIL', nome: 'API SIL', status: 'Ativo', ultima_execucao: null, credencial: 'x', amostra_limite: 1000, tabelas: [] },
        { id: 10, codigo: 'EDUCACAO', nome: 'Educação', status: 'Ativo', ultima_execucao: null, credencial: 'x', amostra_limite: 1000, tabelas: [] },
        { id: 11, codigo: 'DIVIDA', nome: 'Dívida', status: 'Ativo', ultima_execucao: null, credencial: 'x', amostra_limite: 1000, tabelas: [] }],
      execucoes: [], bases: BASES, totais: {}, carga_habilitada: true }]
      : url === '/api/agenda' ? [200, AGENDA] : null)
    const { container } = montar()
    await screen.findByRole('heading', { name: /API SIL/ })
    expect(container.querySelector('#fonte-sil')).not.toBeNull()
    expect(container.querySelector('#fonte-educacao')).not.toBeNull()
    expect(container.querySelectorAll('#fonte-divida')).toHaveLength(1)
  })

  it('selo âmbar + "Cancelar execução pendente" faz PUT {forcar:false}; 404/422 = mensagem amigável', async () => {
    const puts: string[] = []
    let resposta: [number, unknown] = [200, { ok: true }]
    mock((url, init) => {
      if (url === '/api/agenda/divida' && init?.method === 'PUT') { puts.push(String(init.body)); return resposta }
      if (url === '/api/agenda') return [200, AGENDA]
      return null
    })
    montar()
    const c = await cartao('Dívida ativa')
    expect(await within(c).findByText('Execução pendente: carga completa')).toBeVisible()
    resposta = [404, { detail: 'Not Found' }]
    await userEvent.click(within(c).getByRole('button', { name: 'Cancelar execução pendente de Dívida ativa' }))
    expect(await within(c).findByText(SEM_SUPORTE_CANCELAR)).toBeVisible()
    expect(puts).toEqual([JSON.stringify({ forcar: false })])
    resposta = [422, { detail: [{ msg: 'campo extra' }] }]
    await userEvent.click(within(c).getByRole('button', { name: 'Cancelar execução pendente de Dívida ativa' }))
    await waitFor(() => expect(puts).toHaveLength(2))
    expect(within(c).getByText(SEM_SUPORTE_CANCELAR)).toBeVisible()
    expect(SEM_SUPORTE_CANCELAR).toBe('Não foi possível cancelar a execução pendente. Ela continua agendada.')
    expect(within(c).getByText('Execução pendente: carga completa')).toBeVisible()
  })

  it('"Aprovar amostra" com execução pendente pede confirmação com foco em Cancelar', async () => {
    const aprovou = vi.fn()
    mock((url, init) => {
      if (url === '/api/agenda') return [200, AGENDA]
      if (url.endsWith('/amostra/77/aprovar') && init?.method === 'POST') { aprovou(); return [200, { ...AM_OK, aprovada: true }] }
      return null
    })
    const { container } = montar()
    const c = await cartao('Dívida ativa')
    await within(c).findByText('Execução pendente: carga completa')
    await userEvent.click(within(c).getByRole('button', { name: 'Aprovar amostra de Dívida ativa' }))
    const d = await screen.findByRole('alertdialog', { name: confirmaPendente('Dívida ativa') })
    expect(within(d).getByRole('button', { name: 'Cancelar' })).toHaveFocus()
    await userEvent.click(within(d).getByRole('button', { name: 'Cancelar' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(aprovou).not.toHaveBeenCalled()
    await userEvent.click(within(c).getByRole('button', { name: 'Aprovar amostra de Dívida ativa' }))
    await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Aprovar e iniciar' }))
    await waitFor(() => expect(aprovou).toHaveBeenCalledTimes(1))
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')).toEqual([])
  })

  // ---------- UX57-2 / UX57-3 ----------
  it('UX57-2: diálogo "Aprovar e iniciar a carga completa de <fonte>?", Cancelar com foco e sem "Continuar"', async () => {
    mock((url) => (url === '/api/agenda' ? [200, AGENDA] : null))
    montar()
    const c = await cartao('Dívida ativa')
    await within(c).findByText('Execução pendente: carga completa')
    await userEvent.click(within(c).getByRole('button', { name: 'Aprovar amostra de Dívida ativa' }))
    const d = await screen.findByRole('alertdialog', { name: 'Aprovar e iniciar a carga completa de Dívida ativa?' })
    expect(within(d).getAllByRole('button').map((b) => b.textContent)).toEqual(['Cancelar', 'Aprovar e iniciar'])
    expect(within(d).getByRole('button', { name: 'Cancelar' })).toHaveFocus()
    expect(screen.queryByText(/Continuar/)).toBeNull()
  })

  it('UX57-3: selo e "Cancelar execução pendente" num contêiner flex com gap >= 8 px', async () => {
    mock((url) => (url === '/api/agenda' ? [200, AGENDA] : null))
    montar()
    const c = await cartao('Dívida ativa')
    const selo = await within(c).findByText('Execução pendente: carga completa')
    const btn = within(c).getByRole('button', { name: 'Cancelar execução pendente de Dívida ativa' })
    expect(selo.parentElement).toBe(btn.parentElement)
    expect(selo.parentElement).toHaveClass('pendente')
    const css = readFileSync(resolve(__dirname, '../styles.css'), 'utf8')
    const regra = /\.pendente\s*\{([^}]*)\}/.exec(css)?.[1] ?? ''
    expect(regra).toMatch(/display:\s*flex/)
    expect(regra).toMatch(/flex-wrap:\s*wrap/)
    expect(Number(/gap:\s*(\d+)px/.exec(regra)?.[1])).toBeGreaterThanOrEqual(8)
  })
})

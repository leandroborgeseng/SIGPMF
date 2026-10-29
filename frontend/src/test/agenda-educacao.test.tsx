import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AgendaCargas, { type RespAgenda } from '../AgendaCargas'
import { salvaSessao } from '../api'

// Dados sintéticos ZZTESTE (nenhum dado real).
const EX = { id: 91, status: 'sucesso', etapa: null, modo: 'amostra', iniciado_em: '2026-10-28T13:00:00Z',
  finalizado_em: '2026-10-28T13:02:00Z', lidos: 0, gravados: 0, processadas_etapa: null, total_etapa: null }
const edu = (o: object = {}) => ({
  conector: 'educacao', nome: 'Educação (GestaoEscolar + CoreSSO)', horario: '04:00', ativo: false, ordem: 7, forcar: false,
  ultimo_status: null, ultimo_status_texto: null, ultima_mensagem: null, ultimo_inicio_em: null, ultimo_fim_em: null,
  tentativas: 0, proxima_em: null, horario_fora_da_janela: false, ultima_execucao: null, em_andamento: null,
  ultima_execucao_real: EX, pode_executar_agora: true, motivo_executar_agora: null, ...o,
})
const resp = (o: object = {}): RespAgenda => ({
  habilitada: true, janela: '00:00-06:00', dia_todo: false, fuso: 'America/Sao_Paulo', pular_sem_mudanca: true,
  alerta_email: false, agora: '2026-10-28T15:00:00Z', itens: [edu(o)], historico: [],
})
function mock(r: (u: string, i?: RequestInit) => [number, unknown]) {
  const f = vi.fn(async (u: string, i?: RequestInit) => { const [s, c] = r(u, i); return new Response(JSON.stringify(c), { status: s, headers: { 'Content-Type': 'application/json' } }) })
  vi.stubGlobal('fetch', f)
  return f
}
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'admin', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('educação nas fontes prioritárias', () => {
  it('cartão com status real, 0 linhas, agenda desligada em cinza e "Sem agendamento"', async () => {
    mock(() => [200, resp()])
    render(<AgendaCargas ehAdmin />)
    const card = (await screen.findByRole('heading', { name: 'Educação (GestaoEscolar + CoreSSO)' })).closest('article') as HTMLElement
    expect(within(card).getByText('Agenda desligada')).toHaveClass('neutro')
    expect(within(card).queryByText('Ativo')).toBeNull()
    expect(within(card).getByText(/Concluída · nenhuma linha nova/)).toBeVisible()  // texto, não só cor
    expect(within(card).queryByText(/0 linhas lidas/)).toBeNull()
    expect(within(card).getByText('Diária')).toBeVisible()
    expect(within(card).getByText('Sem agendamento')).toBeVisible()
    expect(within(card).getByRole('switch')).not.toBeChecked()
    expect(within(card).getByRole('button', { name: 'Executar agora' })).toBeDisabled()
    expect(within(card).getByText('Ligue a agenda para executar agora.')).toBeVisible()
    expect(screen.queryByRole('row', { name: /Educação/ })).toBeNull()  // não duplica na tabela
    const r = await axe.run(card)
    expect(r.violations).toEqual([])
  })

  it('ligada: próxima execução, liga/desliga por teclado e executa agora (POST)', async () => {
    const f = mock((_u, i) => [i?.method === 'POST' ? 202 : 200, resp({ ativo: true, proxima_em: '2026-10-29T07:00:00Z' })])
    render(<AgendaCargas ehAdmin />)
    expect(await screen.findByText(/Próxima execução: 29\/10 04:00/)).toBeVisible()
    expect(screen.getByText('Agenda ligada')).toBeVisible()
    const sw = screen.getByRole('switch')
    sw.focus(); await userEvent.keyboard(' ')
    expect(f.mock.calls.some(([u, i]) => u.endsWith('/agenda/educacao') && i?.method === 'PUT' && i.body === '{"ativo":false}')).toBe(true)
    const b = await screen.findByRole('button', { name: 'Executar agora' })
    b.focus(); await userEvent.keyboard('{Enter}')
    expect(f.mock.calls.some(([u, i]) => u.endsWith('/agenda/educacao/executar-agora') && i?.method === 'POST')).toBe(true)
  })

  it('em andamento: selo com início e botão desabilitado com motivo', async () => {
    mock(() => [200, resp({ ativo: true, pode_executar_agora: false, motivo_executar_agora: 'Já existe uma carga de educação em andamento.',
      em_andamento: { ...EX, status: 'em_andamento', iniciado_em: '2026-10-28T14:30:00Z', finalizado_em: null } })])
    render(<AgendaCargas ehAdmin />)
    expect(await screen.findByText(/Em andamento · desde 28\/10 11:30/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Executar agora' })).toBeDisabled()
    expect(screen.getByText('Carga em andamento.')).toBeVisible()
  })

  it('fora da janela: motivo escrito ao lado do botão', async () => {
    mock(() => [200, ({ ...resp({ ativo: true, pode_executar_agora: false,
      motivo_executar_agora_codigo: 'fora_janela', motivo_executar_agora: 'x' }), janela: '22:00-05:00' })])
    render(<AgendaCargas ehAdmin />)
    expect(await screen.findByText('Fora da janela noturna (22:00–05:00). Disponível a partir das 22:00.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Executar agora' })).toBeDisabled()
  })

  it('com linhas lidas mostra a contagem', async () => {
    mock(() => [200, resp({ ultima_execucao_real: { ...EX, lidos: 1234 } })])
    render(<AgendaCargas ehAdmin />)
    expect(await screen.findByText(/1\.234 linhas lidas/)).toBeVisible()
  })

  it('sem execução: "Ainda não rodou"; leitura não vê botão nem chave', async () => {
    mock(() => [200, resp({ ultima_execucao_real: null })])
    render(<AgendaCargas ehAdmin={false} />)
    expect(await screen.findByText('Ainda não rodou')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Executar agora' })).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('carregando e erro', async () => {
    mock(() => [500, { detail: 'x' }])
    render(<AgendaCargas ehAdmin />)
    expect(screen.getByText('Carregando agenda…')).toBeVisible()
    expect(await screen.findByText('Sem leitura da agenda.')).toBeVisible()
  })
})

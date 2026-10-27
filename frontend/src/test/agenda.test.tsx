import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AgendaCargas, { classeStatus, type RespAgenda } from '../AgendaCargas'
import { salvaSessao } from '../api'

const item = (o: object = {}) => ({
  conector: 'saude', nome: 'Saúde', horario: '01:00', ativo: true, ordem: 1, forcar: false,
  ultimo_status: 'sucesso', ultimo_status_texto: 'Concluída', ultima_mensagem: 'Carga de Saúde concluída: 13 lidas, 13 gravadas.',
  ultimo_inicio_em: '2026-10-28T04:00:00Z', ultimo_fim_em: '2026-10-28T04:20:00Z', tentativas: 0,
  proxima_em: '2026-10-29T04:00:00Z', horario_fora_da_janela: false, ultima_execucao: null, em_andamento: null, ...o,
})
const resp = (o: Partial<RespAgenda> = {}): RespAgenda => ({
  habilitada: true, janela: '00:00-24:00', dia_todo: true, fuso: 'America/Sao_Paulo', pular_sem_mudanca: true,
  alerta_email: false, agora: '2026-10-28T12:00:00Z',
  itens: [item(), item({ conector: 'cemiterio', nome: 'Cemitério', horario: '03:00', ativo: false, ultimo_status: 'sem_mudanca',
    ultimo_status_texto: 'Sem mudança na origem', ultima_mensagem: 'A origem de Cemitério não mudou desde a última carga: nada foi lido.' })],
  historico: [{ conector: 'saude', id: 7, status: 'sucesso', etapa: 'concluida', iniciado_em: '2026-10-28T04:00:00Z',
    finalizado_em: '2026-10-28T04:20:00Z', lidos: 13, gravados: 13, processadas_etapa: null, total_etapa: null }],
  ...o,
})

function mock(rota: (url: string, init?: RequestInit) => [number, unknown]) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const [s, c] = rota(url, init)
    return new Response(JSON.stringify(c), { status: s, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}

beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'admin', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('agenda das cargas (AG1–AG6)', () => {
  it('mostra janela, status, próxima, última carga e a linha do tempo', async () => {
    mock(() => [200, resp()])
    render(<AgendaCargas ehAdmin={false} />)
    expect(await screen.findByText(/Ligada · dia todo/)).toBeVisible()
    const linha = screen.getByRole('row', { name: /Saúde/ })
    expect(within(linha).getByText('Concluída')).toBeVisible()
    expect(within(linha).getByText(/13 lidas, 13 gravadas/)).toBeVisible()
    expect(screen.getByText('Sem mudança na origem')).toBeVisible()
    expect(screen.getByRole('img', { name: '1 execução(ões) da agenda nas últimas 24 horas' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Executar agora' })).toBeNull()   // leitura não altera
    expect(screen.queryByLabelText(/Horário da carga/)).toBeNull()
  })

  it('admin liga/desliga e altera o horário (PUT) e executa agora (POST)', async () => {
    const f = mock((u, init) => {
      if (init?.method === 'PUT') return [200, resp({ itens: [item({ ativo: false }), resp().itens[1]] })]
      if (init?.method === 'POST') return [202, resp({ itens: [item({ forcar: true }), resp().itens[1]] })]
      return [200, resp()]
    })
    render(<AgendaCargas ehAdmin />)
    const linha = await screen.findByRole('row', { name: /Saúde/ })
    await userEvent.click(within(linha).getByRole('checkbox', { name: /Ligada para Saúde/ }))
    await waitFor(() => expect(f).toHaveBeenCalledWith('/api/agenda/saude', expect.objectContaining({ method: 'PUT', body: '{"ativo":false}' })))
    expect(await screen.findByText('Agenda de Saúde desligada.')).toBeVisible()

    const hora = screen.getAllByLabelText(/Horário da carga de Saúde/)[0] as HTMLInputElement
    await userEvent.clear(hora)
    await userEvent.type(hora, '02:30')
    hora.blur()
    await waitFor(() => expect(f).toHaveBeenCalledWith('/api/agenda/saude', expect.objectContaining({ body: '{"horario":"02:30"}' })))

    await userEvent.click(within(screen.getByRole('row', { name: /Saúde/ })).getByRole('button', { name: 'Executar agora' }))
    await waitFor(() => expect(f).toHaveBeenCalledWith('/api/agenda/saude/executar-agora', expect.objectContaining({ method: 'POST' })))
    expect(await screen.findByText(/começa em até 1 minuto/)).toBeVisible()
  })

  it('agenda desligada: aviso de como ligar e botão desabilitado com motivo', async () => {
    mock(() => [200, resp({ habilitada: false, dia_todo: false, janela: '00:00-06:00' })])
    render(<AgendaCargas ehAdmin />)
    expect(await screen.findByText('Desligada neste ambiente')).toBeVisible()
    expect(screen.getByText(/AGENDA_HABILITADA=true/)).toBeVisible()
    const b = within(screen.getByRole('row', { name: /Saúde/ })).getByRole('button', { name: 'Executar agora' })
    expect(b).toBeDisabled()
    expect(b).toHaveAttribute('title', 'Agenda desligada neste ambiente')
  })

  it('carga em andamento mostra a etapa e o progresso', async () => {
    const rodando = { id: 9, status: 'em_andamento', etapa: 'prata', iniciado_em: '2026-10-28T11:50:00Z', finalizado_em: null,
      lidos: 0, gravados: 0, processadas_etapa: 1200, total_etapa: 5000 }
    mock(() => [200, resp({ itens: [item({ em_andamento: rodando }), resp().itens[1]] })])
    render(<AgendaCargas ehAdmin />)
    const linha = await screen.findByRole('row', { name: /Saúde/ })
    expect(within(linha).getByText('Tratando (prata)')).toBeVisible()
    expect(within(linha).getByText('1.200 de 5.000')).toBeVisible()
    expect(within(linha).getByRole('button', { name: 'Executar agora' })).toBeDisabled()
  })

  it('resposta inválida ou erro não quebra a tela', async () => {
    mock(() => [200, { qualquer: 'coisa' }])
    const { unmount } = render(<AgendaCargas ehAdmin />)
    expect(await screen.findByText('Sem leitura da agenda.')).toBeVisible()
    unmount()
    mock(() => [500, { detail: 'erro' }])
    render(<AgendaCargas ehAdmin />)
    expect(await screen.findByText('Sem leitura da agenda.')).toBeVisible()
  })

  it('classes de status e acessibilidade (axe)', async () => {
    expect(classeStatus('falha')).toBe('falha')
    expect(classeStatus('ocupada')).toBe('atencao')
    expect(classeStatus('sem_mudanca')).toBe('neutro')
    mock(() => [200, resp()])
    const { container } = render(<AgendaCargas ehAdmin />)
    await screen.findByRole('row', { name: /Saúde/ })
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })
})

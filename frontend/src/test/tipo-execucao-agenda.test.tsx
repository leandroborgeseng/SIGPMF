import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AgendaCargas from '../AgendaCargas'
import PainelProgresso from '../PainelProgresso'
import { salvaSessao } from '../api'

// PR-A: CT15/UXP9 (tipo real e teto de lotes) e agenda desligada sem "próxima". Dados mockados ZZTESTE.
const fonte = (o: object) => ({ id: 'sil', nome: 'SIL', status: 'concluida', status_texto: 'Concluída', motivo_codigo: null, motivo: null, falta: null,
  aguardando_em: null, aguardando_em_brt: null, progresso: null, tabelas: [], ultima_execucao: null, ...o })
function mock(c: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(c), { status: 200, headers: { 'Content-Type': 'application/json' } })))
}
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'admin', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('tipo real da execução e agenda desligada', () => {
  it('UXP9: amostra que parou no teto mostra o aviso e nunca "Carga completa"', async () => {
    mock({ agora: '2026-10-30T00:29:00Z', rodando: false, atualizar_a_cada_s: 30, fontes: [fonte({
      ultima_execucao: { execucao_id: 9, finalizado_em: '2026-10-30T00:20:00Z', lidas: 49, gravadas: 49, lotes: 1, modo: 'Amostra',
        texto_limite: 'Amostra: 49 linhas (parou no limite de 1 lote). Não é o total da origem.' } })] })
    render(<PainelProgresso />)
    expect(await screen.findByText(/Amostra: 49 linhas \(parou no limite de 1 lote\)\. Não é o total da origem\./)).toBeInTheDocument()
    expect(screen.queryByText(/Carga completa/)).toBeNull()
  })

  it('CT15: sem teto, mostra o tipo vindo da execução', async () => {
    mock({ agora: '2026-10-30T00:29:00Z', rodando: false, atualizar_a_cada_s: 30, fontes: [fonte({
      ultima_execucao: { execucao_id: 9, finalizado_em: null, lidas: 10, gravadas: 10, lotes: 1, modo: 'Amostra', texto_limite: null } })] })
    render(<PainelProgresso />)
    expect(await screen.findByText('Última execução (Amostra): 10 linhas processadas')).toBeInTheDocument()
  })

  it('agenda desligada: card mostra "Agenda desligada" e nunca "próxima às"', async () => {
    mock({ agora: '2026-10-30T00:29:00Z', rodando: false, atualizar_a_cada_s: 30, fontes: [fonte({
      status: 'pausada', status_texto: 'Pausada', motivo_codigo: 'agenda_desligada', motivo: 'Agenda desligada', aguardando_em_brt: '21:29' })] })
    render(<PainelProgresso />)
    expect(await screen.findByText(/Agenda desligada/)).toBeInTheDocument()
    expect(screen.queryByText(/próxima às/)).toBeNull()
  })

  it('agenda: linha desligada com "Executar agora" pendente mostra "Agenda desligada" na coluna Próxima', async () => {
    mock({ habilitada: true, janela: '00:00-06:00', dia_todo: false, fuso: 'America/Sao_Paulo', pular_sem_mudanca: true, alerta_email: false,
      agora: '2026-10-30T00:29:00Z', completa_a_cada_dias: 7, historico: [], itens: [{ conector: 'sil', nome: 'SIL', horario: '05:30', ativo: false,
        ordem: 6, forcar: true, ultimo_status: null, ultimo_status_texto: null, ultima_mensagem: null, ultimo_inicio_em: null, ultimo_fim_em: null,
        tentativas: 0, proxima_em: '2026-10-30T00:29:00Z', horario_fora_da_janela: false, em_andamento: null, ultima_execucao: null }] })
    render(<AgendaCargas ehAdmin />)
    const cel = (await screen.findAllByText('Agenda desligada')).map((e) => e.closest('td')).filter(Boolean)
    expect(cel.some((td) => td?.getAttribute('data-rotulo') === 'Próxima')).toBe(true)
    for (const td of screen.getAllByRole('cell').filter((c) => c.getAttribute('data-rotulo') === 'Próxima'))
      expect(within(td).queryByText(/Em até 1 minuto|21:29/)).toBeNull()
  })
})

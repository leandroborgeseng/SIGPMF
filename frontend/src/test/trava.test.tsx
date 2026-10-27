import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AvisoBloqueio, type Bloqueio } from '../pages/PainelBases'

const BL: Bloqueio = {
  execucao_id: 7, tipo: 'amostra', tipo_rotulo: 'amostra', iniciado_em: '2026-10-26T19:30:00Z', atualizado_em: '2026-10-26T19:31:00Z',
  viva: true, parada_min: 1, alerta_min: 5, parada: false, mensagem: 'Já existe uma amostra de Cemitério em andamento desde 26/10 16:30',
}

afterEach(() => vi.unstubAllGlobals())

describe('CC12: aviso de execução que bloqueia', () => {
  it('mostra tipo, início e última atividade; Interromper desabilitado com motivo enquanto há atividade', async () => {
    const { container } = render(<AvisoBloqueio bl={BL} nome="Cemitério" conector="cemiterio" ehAdmin onFim={() => {}} />)
    const st = screen.getByRole('status')
    expect(st).toHaveTextContent('Amostra de Cemitério em andamento')
    expect(st).toHaveTextContent('iniciada em')
    expect(st).toHaveTextContent('última atividade')
    expect(screen.getByRole('button', { name: 'Interromper' })).toBeDisabled()
    expect(st).toHaveTextContent('Disponível depois de 5 min sem atividade')
    expect((await axe.run(container)).violations).toEqual([])
  })

  it('operador vê o aviso mas não vê Interromper', () => {
    render(<AvisoBloqueio bl={{ ...BL, parada: true, parada_min: 30 }} nome="Cemitério" conector="cemiterio" ehAdmin={false} onFim={() => {}} />)
    expect(screen.getByRole('status')).toHaveTextContent('Sem atividade há 30 min (limite 5 min)')
    expect(screen.queryByRole('button', { name: 'Interromper' })).toBeNull()
  })

  it('admin interrompe execução parada com confirmação e motivo (POST com motivo no corpo)', async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ id: 7, status: 'interrompida' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetch)
    const onFim = vi.fn()
    render(<AvisoBloqueio bl={{ ...BL, tipo: 'completa', parada: true, viva: false, parada_min: 42 }} nome="Saúde" conector="saude" ehAdmin onFim={onFim} />)
    expect(screen.getByRole('status')).toHaveTextContent('Carga completa de Saúde em andamento')
    await userEvent.click(screen.getByRole('button', { name: 'Interromper' }))
    const confirmar = screen.getByRole('button', { name: 'Confirmar interrupção' })
    expect(confirmar).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Motivo'), 'ZZTESTE parada após reinício')
    await userEvent.click(confirmar)
    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/conectores/saude/execucoes/7/interromper')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ motivo: 'ZZTESTE parada após reinício' })
    expect(onFim).toHaveBeenCalled()
  })
})

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CargaAudesp from '../CargaAudesp'

const ESTADO = {
  anos: [2025, 2026], municipio: 'franca', em_andamento: null,
  ultima_execucao: { id: 9, status: 'sucesso', mensagem: 'Carregados: despesas 2026 até o mês 8.', arquivo_atual: null, lidos: 10, iniciado_em: '2026-11-03T20:00:00Z', finalizado_em: '2026-11-03T20:01:00Z' },
  arquivos: [
    { tipo: 'despesas', ano: 2026, origem: 'tce', linhas: 102958, ultimo_mes: 8, publicado_tce: 'Wed, 23 Sep 2026 12:02:03 GMT', carregado_em: '2026-11-03T20:01:00Z' },
    { tipo: 'receitas', ano: 2026, origem: 'upload', linhas: 2111, ultimo_mes: 8, publicado_tce: null, carregado_em: '2026-11-03T20:00:00Z' },
  ],
}
type Chamada = { url: string; init?: RequestInit }

function mock(resp: (c: Chamada) => [number, unknown]) {
  const chamadas: Chamada[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const c = { url, init }; chamadas.push(c)
    const [s, b] = resp(c)
    return new Response(JSON.stringify(b), { status: s, headers: { 'Content-Type': 'application/json' } })
  }))
  return chamadas
}
afterEach(() => vi.unstubAllGlobals())

describe('AUDESP na tela Conectores (AU1)', () => {
  it('mostra o que está carregado por ano e arquivo, e o que falta', async () => {
    mock(() => [200, ESTADO])
    const { container } = render(<CargaAudesp ehAdmin={false} />)
    const tabela = await screen.findByRole('table', { name: 'Arquivos do AUDESP carregados' })
    const linhas = within(tabela).getAllByRole('row').slice(1).map((r) => r.textContent)
    expect(linhas[1]).toContain('Despesas102.958ago/202623/09/2026')
    expect(linhas[0]).toContain('Receitas2.111ago/2026enviado manualmente')
    expect(linhas.filter((l) => l?.includes('Ainda não carregado'))).toHaveLength(4)  // restos 2026 + 3 de 2025
    expect(screen.getByText(/Concluída/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Carregar agora' })).toBeNull()  // só admin
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })

  it('admin: Carregar agora (com forçar), acompanhamento e envio do arquivo', async () => {
    const rodando = { ...ESTADO, em_andamento: { ...ESTADO.ultima_execucao, status: 'em_andamento', arquivo_atual: 'despesas 2025' } }
    const chamadas = mock((c) => c.url.endsWith('/audesp/carregar') ? [202, rodando]
      : c.url.endsWith('/audesp/arquivo') ? [422, { detail: 'Este arquivo não parece ser do AUDESP (receitas): coluna \'ds_tipo\' ausente.' }]
      : [200, ESTADO])
    render(<CargaAudesp ehAdmin />)
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Baixar de novo mesmo sem mudança no TCE' }))
    await userEvent.click(screen.getByRole('button', { name: 'Carregar agora' }))
    expect(await screen.findByText('Carregando · despesas 2025')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Carregar agora' })).toBeDisabled()
    const post = chamadas.find((c) => c.url.endsWith('/audesp/carregar'))
    expect(JSON.parse(String(post?.init?.body))).toEqual({ forcar: true })
    // durante a carga, o envio de arquivo fica bloqueado
    const input = screen.getByLabelText('Enviar arquivo do TCE (.zip ou .csv)')
    expect(input).toBeDisabled()  // durante a carga não envia
  })

  it('resposta fora do formato mostra erro no card sem quebrar a página', async () => {
    mock(() => [200, { qualquer: 'coisa' }])
    render(<CargaAudesp ehAdmin />)
    expect(await screen.findByText('Não foi possível carregar o estado do AUDESP.')).toBeVisible()
  })

  it('admin: arquivo recusado mostra o motivo', async () => {
    mock((c) => c.url.endsWith('/audesp/arquivo') ? [422, { detail: 'Este arquivo não parece ser do AUDESP (receitas): coluna \'ds_tipo\' ausente.' }] : [200, ESTADO])
    render(<CargaAudesp ehAdmin />)
    const input = await screen.findByLabelText('Enviar arquivo do TCE (.zip ou .csv)')
    await userEvent.upload(input, new File(['a;b\n1;2\n'], 'x.csv', { type: 'text/csv' }))
    expect(await screen.findByText(/não parece ser do AUDESP/)).toBeVisible()
  })
})

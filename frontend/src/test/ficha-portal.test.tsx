import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { AcessosCidadao, LinhaDoTempoCidadao, ResumoPortal, SaudeCidadao, pendencias } from '../FichaPortal'

const trib = {
  cidadao_id: 'c1',
  totais: { imoveis: 1, em_aberto: 368.6, parcelas_em_aberto: 2, vencido: 184.3, parcelas_vencidas: 1, divida_ativa: 646.2, inscricoes_divida: 1, protestada: true },
  imoveis: [], dividas: [],
  lancamentos: [{ exercicio: 2026, tributo: '001', parcela: 9, codi: 'x', vencimento: '2099-11-05', valor: 184.3, valor_pago: 0, pagamento: null, pago: false, situacao: 'a_vencer' }],
}
const saude = {
  cidadao_id: 'c1',
  proximos: [{ data: '2099-11-12', unidade: 'Ambulatório', especialidade: 'Oftalmologia', dias_espera: 30 }],
  atendimentos: [{ data: '2026-10-01', unidade: 'UBS 02', especialidade: 'Clinica Geral' }],
  vacinas: [{ data: '2026-10-15', vacina: 'Influenza', dose: 'D1', unidade: 'UBS 02' }],
}
const mock = (rota: (u: string) => [number, unknown]) => {
  const f = vi.fn(async (u: string) => { const [s, c] = rota(u); return new Response(JSON.stringify(c), { status: s, headers: { 'Content-Type': 'application/json' } }) })
  vi.stubGlobal('fetch', f)
  return f
}
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'admin_lgpd', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('ficha no formato do portal (onda 5)', () => {
  it('pendências: vencidas, dívida, próxima parcela e próxima consulta', () => {
    const p = pendencias(trib, saude)
    expect(p.map((x) => x.titulo)).toEqual(['1 parcela(s) vencida(s)', 'Débito em dívida ativa', 'Próxima parcela vence em 05/11/2099',
      'Consulta de Oftalmologia em 12/11/2099'])
    expect(pendencias(false, false)).toEqual([])
    const comEmpresa = pendencias({ ...trib, empresas: [{ ccm: 77, nome: 'X', documento: '*', possui_debito: true, endereco: null, bairro: null }] }, false)
    expect(comEmpresa.find((x) => x.area === 'Empresas')?.detalhe).toBe('CCM 77')
  })
  it('resumo e saúde sem conteúdo clínico, acessível', async () => {
    const { container } = render(<><ResumoPortal trib={trib} saude={saude} /><SaudeCidadao saude={saude} /></>)
    expect(within(screen.getByRole('list', { name: 'Pendências' })).getAllByRole('listitem')).toHaveLength(4)
    expect(screen.getByText(/Influenza D1/)).toBeVisible()
    expect(screen.getByText('Diagnósticos e anotações clínicas não aparecem aqui.')).toBeVisible()
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })
  it('linha do tempo pagina com "Carregar mais"', async () => {
    const ev = (i: number) => ({ data: `2026-10-${String(28 - i).padStart(2, '0')}`, area: 'saude', tipo: 'consulta', titulo: `Evento ${i}`, fonte: 'Saúde' })
    const f = mock((u) => [200, { eventos: u.includes('antes=') ? [ev(25)] : Array.from({ length: 20 }, (_, i) => ev(i)) }])
    render(<LinhaDoTempoCidadao id="c1" />)
    expect(await screen.findByText('Evento 0')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Carregar mais' }))
    expect(await screen.findByText('Evento 25')).toBeVisible()
    expect(f.mock.calls.some(([u]) => String(u).includes('antes=2026-10-09'))).toBe(true)
    expect(screen.queryByRole('button', { name: 'Carregar mais' })).toBeNull()
  })
  it('acessos só para admin/admin_lgpd e sob demanda', async () => {
    mock(() => [200, { acessos: [{ usuario: 'leitura@pmf', acao: 'consulta_ficha', o_que: 'Abriu a ficha', quando: '2026-10-28T10:00:00Z' }] }])
    const { unmount } = render(<AuthProvider><AcessosCidadao id="c1" /></AuthProvider>)
    await userEvent.click(screen.getByRole('button', { name: 'Ver acessos' }))
    expect(await screen.findByText(/leitura@pmf · Abriu a ficha/)).toBeVisible()
    unmount()
    salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' })
    const { container } = render(<AuthProvider><AcessosCidadao id="c1" /></AuthProvider>)
    expect(container).toBeEmptyDOMElement()
  })
})

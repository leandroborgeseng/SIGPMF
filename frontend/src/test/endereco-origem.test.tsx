import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import OrigemDado, { idade, nomeFonte } from '../OrigemDado'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'

// EN1, EN5, EN6, EN8, EN9 e UXO1–UXO9. Só dados ZZTESTE mockados; nada real.
const L1 = 'Rua ZZTESTE DAS FLORES, 4071 – ap 12'
const L2 = 'ZZTESTE Centro · Franca/SP · CEP 14405-457'
const FICHA = {
  cidadao_id: 'zz-cid-1', nome: 'ZZTESTE PACIENTE', cpf: '***.456.789-**', cpf_mascarado: true, sistemas: ['SAUDE', 'IPTU'],
  dominios: ['identificacao', 'endereco'],
  dados: [
    { dominio: 'endereco', campo: 'endereco', valor: `${L1}\n${L2}`, fonte: 'SAUDE', atualizado_em: '2026-02-20T10:00:00Z' },
    { dominio: 'identificacao', campo: 'nome', valor: 'ZZTESTE PACIENTE', fonte: 'IPTU', atualizado_em: '2024-02-10T10:00:00Z' },
    { dominio: 'identificacao', campo: 'nome_mae', valor: null, fonte: 'SAUDE', atualizado_em: '2026-02-20T10:00:00Z' },
  ],
  enderecos: [
    { valor: `${L1}\n${L2}`, linhas: [L1, L2], fonte: 'SAUDE', atualizado_em: '2026-02-20T10:00:00Z', mais_recente: true, origem_endereco: 'geral' },
    { valor: 'Rua não informada na origem, 10\nCEP 14400-000', linhas: ['Rua não informada na origem, 10', 'CEP 14400-000'], fonte: 'DIVIDA', atualizado_em: '2025-12-01T10:00:00Z', mais_recente: false, origem_endereco: null },
    { valor: 'ZZTESTE Av. Brasil, 10', linhas: ['ZZTESTE Av. Brasil, 10'], fonte: 'NOVAFONTE', atualizado_em: '2025-11-01T10:00:00Z', mais_recente: false, origem_endereco: 'outro' },
  ],
}

function mockFetch() {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const [st, corpo] = url === '/api/gold/cidadaos/zz-cid-1' ? [200, FICHA] : [404, { detail: 'não mockado' }]
    return new Response(JSON.stringify(corpo), { status: st, headers: { 'Content-Type': 'application/json' } })
  }))
}
const abrir = async () => {
  mockFetch()
  salvaSessao({ token: 't', perfil: 'leitura', nome: 'ZZTESTE' })
  render(<MemoryRouter initialEntries={['/simulador?cid=zz-cid-1']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
  await screen.findByRole('heading', { name: 'ZZTESTE PACIENTE' })
}
const campo = (rotulo: string) => screen.getAllByText(rotulo, { selector: 'dt' })[0].closest('.campo') as HTMLElement

beforeEach(() => { sessionStorage.clear(); vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date('2026-10-30T09:00:00Z')) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('OrigemDado (UXO2–UXO4, UXO8)', () => {
  it('UXO3: nome por extenso pelo mapa fixo; fora do mapa, código capitalizado', () => {
    expect(['SAUDE', 'IPTU', 'DIVIDA', 'SIL', 'EDUCACAO', 'CADUNICO', 'FINANCEIRO'].map(nomeFonte)).toEqual(
      ['Saúde', 'IPTU', 'Dívida Ativa', 'SIL (empresas)', 'Educação', 'CadÚnico', 'Financeiro'])
    expect(nomeFonte('NOVAFONTE')).toBe('Novafonte')
  })
  it('UXO2/UXO4/UXO8: chip "Saúde · atualizado em 20/02/2026", leitor ouve a data por extenso, sem foco', () => {
    render(<OrigemDado fonte="SAUDE" atualizadoEm="2026-02-20T10:00:00Z" agora={new Date(2026, 9, 30)} />)
    const chip = screen.getByRole('note', { name: 'Fonte: Saúde, atualizado em 20 de fevereiro de 2026' })
    expect(chip).toHaveTextContent('Saúde · atualizado em 20/02/2026')
    expect(chip).toHaveClass('origem-dado')
    expect(chip).not.toHaveClass('antigo')
    expect(chip).not.toHaveAttribute('tabindex')
  })
  it('UXO4/EN8: mais de 12 meses fica âmbar com relógio, "há 1 ano e 8 meses" e a data exata no tooltip', () => {
    render(<OrigemDado fonte="IPTU" atualizadoEm="2024-02-10" agora={new Date(2025, 9, 30)} />)
    const chip = screen.getByRole('note', { name: 'Fonte: IPTU, atualizado em 10 de fevereiro de 2024' })
    expect(chip).toHaveClass('antigo')
    expect(chip).toHaveTextContent('IPTU · atualizado há 1 ano e 8 meses')
    expect(chip).toHaveAttribute('title', 'Atualizado em 10/02/2024')
    expect(chip).toHaveAttribute('tabindex', '0')
    expect(idade(new Date(2025, 9, 30), new Date(2026, 9, 30)).meses).toBe(12)  // 12 meses ainda não é âmbar
  })
})

describe('ficha do cidadão: endereço e procedência', () => {
  it('EN1/EN8/UXO1: endereço em 2 linhas e o chip numa linha própria, abaixo do valor', async () => {
    await abrir()
    const c = campo('Endereço')
    const linhas = c.querySelectorAll('.campo-valor .linha')
    expect([...linhas].map((l) => l.textContent)).toEqual([L1, L2])
    const chip = within(c).getAllByRole('note')[0]
    expect(chip).toHaveTextContent('Saúde · atualizado em 20/02/2026')
    expect(chip.closest('.campo-valor')).toBeNull()  // não fica na mesma linha/elemento do valor
    expect(c.textContent).not.toMatch(/Fonte: SAUDE|— Fonte/)
  })
  it('EN9/UXO9: o rótulo "Endereço" aparece uma vez só', async () => {
    await abrir()
    expect(screen.getAllByText('Endereço')).toHaveLength(1)
  })
  it('EN6: todo campo com procedência tem o chip (inclusive nome e o âmbar com mais de 12 meses)', async () => {
    await abrir()
    for (const r of ['Endereço', 'Nome', 'Nome da mãe']) expect(within(campo(r)).getAllByRole('note').length).toBeGreaterThan(0)
    expect(within(campo('Nome')).getByRole('note')).toHaveClass('antigo')
  })
  it('UXO6: sem dado na origem, "Não informado na origem" em itálico com o chip da fonte consultada', async () => {
    await abrir()
    const c = campo('Nome da mãe')
    expect(within(c).getByText('Não informado na origem')).toHaveClass('nao-informado')
    expect(within(c).getByRole('note')).toHaveTextContent('Saúde')
  })
  it('UXO5/EN8: "Ver outros 2 valores" abre valor + chip por fonte', async () => {
    await abrir()
    const c = campo('Endereço')
    const btn = within(c).getByRole('button', { name: 'Ver outros 2 valores' })
    await userEvent.click(btn)
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    const lista = c.querySelector('.outros-valores') as HTMLElement
    expect(within(lista).getByText('Rua não informada na origem, 10')).toBeInTheDocument()
    expect(within(lista).getByRole('note', { name: /Fonte: Dívida Ativa/ })).toBeInTheDocument()
    expect(within(lista).getByRole('note', { name: /Fonte: Novafonte/ })).toBeInTheDocument()
  })
})

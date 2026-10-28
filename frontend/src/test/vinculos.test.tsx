import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import Vinculos from '../pages/Vinculos'

const pf = { id: 'pf:c1', tipo: 'PF', nome: 'Pessoa A', doc: '***.456.789-**', flags: [{ texto: 'Dívida ativa R$ 646,20', tom: 'alerta' }], cidadao_id: 'c1' }
const im = { id: 'im:011', tipo: 'IM', nome: 'Rua Sintetica, 550', doc: 'Inscrição 011', flags: [], cidadao_id: null }
const pj = { id: 'ps:9', tipo: 'PJ', nome: 'Empresa (CNPJ)', doc: '**.***.***/0001-**', flags: [], cidadao_id: null }
const viz = (n: object, relacao: string, grupo: string) => ({ ...n, relacao, fonte: 'IPTU · possuidor', grupo })

function mock() {
  const f = vi.fn(async (u: string) => {
    let c: unknown = []
    if (u.includes('/silver/cidadaos')) c = [{ cidadao_id: 'c1', nome: 'Pessoa A', cpf: '***.456.789-**' }]
    else if (u.includes('/vinculos/pf%3Ac1')) c = { no: pf, vizinhos: [viz(im, 'Proprietário(a) · desde 2015', 'Imóveis')] }
    else if (u.includes('/vinculos/im%3A011')) c = { no: im, vizinhos: [viz(pf, 'Proprietário(a) · desde 2015', 'Possuidores'), viz(pj, 'Possuidor(a) · posse anterior', 'Possuidores')] }
    return new Response(JSON.stringify(c), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'admin_lgpd', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('explorador de vínculos (onda 6)', () => {
  it('exige motivo, abre um nível por clique, mostra o caminho e volta', async () => {
    const f = mock()
    const { container } = render(<MemoryRouter><Vinculos /></MemoryRouter>)
    await userEvent.type(screen.getByLabelText('Pessoa (nome ou CPF completo)'), 'Pessoa')
    expect(screen.getByRole('button', { name: 'Buscar' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Motivo da consulta (obrigatório)'), 'Cobrança de dívida ativa')
    await userEvent.click(screen.getByRole('button', { name: 'Buscar' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Pessoa A' }))
    const col1 = await screen.findByRole('region', { name: 'Ligados a Pessoa A' })
    await userEvent.click(within(col1).getByRole('button', { name: /Rua Sintetica, 550/ }))
    const col2 = await screen.findByRole('region', { name: 'Ligados a Rua Sintetica, 550' })
    expect(within(col2).getByText('Empresa (CNPJ)')).toBeVisible()
    expect(within(col2).getByText(/posse anterior/)).toBeVisible()
    const caminho = screen.getByRole('navigation', { name: 'Caminho percorrido' })
    expect(within(caminho).getAllByRole('button').map((b) => b.textContent)).toEqual(['PFPessoa A', 'IMRua Sintetica, 550', 'Recomeçar'])
    expect(f.mock.calls.some(([u]) => String(u).includes('motivo=Cobran') && String(u).includes('nivel=1'))).toBe(true)
    await userEvent.click(within(caminho).getByRole('button', { name: /Pessoa A/ }))
    expect(screen.queryByRole('region', { name: 'Ligados a Rua Sintetica, 550' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Abrir ficha completa' })).toHaveAttribute('href', '/simulador?cid=c1')
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])
  })
})

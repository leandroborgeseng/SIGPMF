import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import { ColunasMes } from '../paineis/graficos'
import { DrillCubo, doPeriodo, useDetalhe } from '../paineis/Drill'

const CATALOGO = {
  rh: {
    dimensoes: [{ chave: 'ano', nome: 'Ano' }, { chave: 'mes', nome: 'Mês' }, { chave: 'secretaria', nome: 'Secretaria' }, { chave: 'cargo', nome: 'Cargo' },
      { chave: 'faixa_salario', nome: 'Faixa de remuneração', ordem: ['até R$ 3 mil', 'R$ 3 a 5 mil'] }],
    medidas: [{ chave: 'pessoas', nome: 'Pessoas', formato: 'n', aditiva: true }, { chave: 'bruto', nome: 'Folha bruta', formato: 'brl', aditiva: true },
      { chave: 'media', nome: 'Remuneração média', formato: 'brl', aditiva: false }],
  },
}
type Resposta = { linhas: Record<string, unknown>[]; ocultos?: number; calculado?: boolean }
let urls: string[] = []
function mock(dados: (q: URLSearchParams) => Resposta) {
  urls = []
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = new URL(u, 'http://x')
    urls.push(u)
    const corpo = url.pathname.endsWith('/cubos') ? { catalogo: CATALOGO } : { ocultos: 0, truncado: false, calculado: true, ...dados(url.searchParams) }
    return new Response(JSON.stringify(corpo), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
}
const POR_DIM = (q: URLSearchParams): Resposta => {
  const ano = q.get('f_ano')
  const fator = ano === '2024' ? 0.5 : 1
  if (q.get('agrupar') === 'secretaria') return { linhas: [{ secretaria: 'Educação', pessoas: 100 * fator, bruto: 1000 * fator, media: 10 }, { secretaria: 'Saúde', pessoas: 300 * fator, bruto: 2000 * fator, media: 7 }], ocultos: 2 }
  if (q.get('agrupar') === 'cargo') return { linhas: [{ cargo: 'Enfermeiro', pessoas: 40, bruto: 400, media: 10 }] }
  if (q.get('agrupar') === 'faixa_salario') return { linhas: [{ faixa_salario: 'R$ 3 a 5 mil', pessoas: 9, bruto: 40, media: 4 }, { faixa_salario: 'até R$ 3 mil', pessoas: 50, bruto: 100, media: 2 }] }
  return { linhas: [] }
}
const montar = (ui: React.ReactNode) => render(<MemoryRouter><AuthProvider>{ui}</AuthProvider></MemoryRouter>)
beforeEach(() => { sessionStorage.clear(); salvaSessao({ token: 't', perfil: 'leitura', nome: 'T' }) })
afterEach(() => vi.unstubAllGlobals())

describe('drill-down', () => {
  it('desce de nível, leva o filtro, volta pela trilha e compara com o ano anterior', async () => {
    mock(POR_DIM)
    const { container } = montar(<DrillCubo cubo="rh" fixos={{ ano: 2025, mes: 3 }} caminho={['secretaria', 'cargo', 'faixa_salario']} />)
    const saude = await screen.findByRole('button', { name: 'Saúde' })
    const linhas = () => within(screen.getByRole('table')).getAllByRole('rowheader').map((c) => c.textContent)
    expect(linhas()).toEqual(['Saúde', 'Educação', 'Total mostrado'])  // maior primeiro
    expect(within(screen.getByRole('row', { name: /Saúde/ })).getByText('75%')).toBeVisible()
    expect(await within(screen.getByRole('row', { name: /Saúde/ })).findByText('▲ 100%')).toBeVisible()
    expect(screen.getByText('2 grupos ocultos por ter menos de 5 pessoas.')).toBeVisible()
    const r = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(r.violations.map((v) => v.id)).toEqual([])

    await userEvent.click(saude)
    expect(await screen.findByRole('button', { name: 'Enfermeiro' })).toBeVisible()
    const ultima = new URL(urls.filter((u) => u.includes('agrupar=cargo')).at(0) as string, 'http://x').searchParams
    expect(ultima.get('f_secretaria')).toBe('Saúde')
    expect(ultima.get('f_ano')).toBe('2025')
    expect(screen.getByText('Saúde', { selector: 'strong' })).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Início' }))
    expect(await screen.findByRole('button', { name: 'Saúde' })).toBeVisible()
  })

  it('"Abrir por" troca o ângulo, faixa fica na ordem própria e medida muda o destaque', async () => {
    mock(POR_DIM)
    montar(<DrillCubo cubo="rh" fixos={{ ano: 2025, mes: 3 }} caminho={['secretaria']} />)
    await screen.findByRole('button', { name: 'Saúde' })
    await userEvent.click(within(screen.getByRole('group', { name: 'Abrir por' })).getByRole('button', { name: 'Faixa de remuneração' }))
    const ate = await screen.findByRole('button', { name: 'até R$ 3 mil' })
    const linhas = within(screen.getByRole('table')).getAllByRole('rowheader').map((c) => c.textContent)
    expect(linhas.slice(0, 2)).toEqual(['até R$ 3 mil', 'R$ 3 a 5 mil'])
    expect(ate).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Remuneração média' }))
    expect(screen.getByRole('button', { name: 'Remuneração média' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByText('% do total')).toBeNull()  // média não soma
  })

  it('mês só é oferecido com o ano escolhido', async () => {
    mock(POR_DIM)
    montar(<DrillCubo cubo="rh" caminho={['secretaria']} />)
    const grupo = await screen.findByRole('group', { name: 'Abrir por' })
    expect(within(grupo).queryByRole('button', { name: 'Mês' })).toBeNull()
    expect(within(grupo).getByRole('button', { name: 'Ano' })).toBeVisible()
  })

  it('recorte pequeno explica a supressão; cubo sem cálculo aponta para Cruzamentos', async () => {
    mock(() => ({ linhas: [], ocultos: 1 }))
    const { unmount } = montar(<DrillCubo cubo="rh" fixos={{ ano: 2025 }} caminho={['secretaria']} />)
    expect(await screen.findByText(/menos de 5 pessoas e não é mostrado/)).toBeVisible()
    unmount()
    mock(() => ({ linhas: [], calculado: false }))
    montar(<DrillCubo cubo="rh" caminho={['secretaria']} />)
    expect(await screen.findByRole('link', { name: 'Cruzamentos' })).toHaveAttribute('href', '/painel/cruzamentos')
  })

  it('gaveta: abre, foca o fechar, Esc fecha e devolve o foco', async () => {
    mock(POR_DIM)
    function Tela() {
      const { abrir, gaveta } = useDetalhe()
      return <><button type="button" onClick={() => abrir({ titulo: 'Folha de mar/25', cubo: 'rh', fixos: doPeriodo('2025-03'), caminho: ['secretaria'] })}>abre</button>{gaveta}</>
    }
    montar(<Tela />)
    await userEvent.click(screen.getByRole('button', { name: 'abre' }))
    const d = await screen.findByRole('dialog', { name: 'Folha de mar/25' })
    expect(within(d).getByRole('button', { name: 'Fechar detalhe' })).toHaveFocus()
    expect(await within(d).findByRole('button', { name: 'Saúde' })).toBeVisible()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('button', { name: 'abre' })).toHaveFocus()
  })
})

describe('gráfico no tempo', () => {
  const pontos = (n: number) => Array.from({ length: n }, (_, i) => ({ periodo: `20${String(10 + Math.floor(i / 12))}-${String(i % 12 + 1).padStart(2, '0')}`, dim: '', valor: 100 + i }))

  it('setas leem cada período e Enter abre o detalhe', async () => {
    const aoAbrir = vi.fn()
    render(<ColunasMes a={pontos(3)} rotuloA="Consultas" aoAbrir={aoAbrir} />)
    screen.getByRole('img', { name: /Consultas por mês/ }).focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByText('jan/10', { selector: '.grafico-dica-per' })).toBeVisible()
    expect(screen.getByText('clique para detalhar')).toBeVisible()
    await userEvent.keyboard('{ArrowRight}{Enter}')
    expect(aoAbrir).toHaveBeenCalledWith('2010-02')
  })

  it('série longa vira linha; curta fica em colunas', () => {
    const { container, unmount } = render(<ColunasMes a={pontos(30)} rotuloA="X" meses={72} />)
    expect(container.querySelector('.grafico-linha')).not.toBeNull()
    expect(container.querySelectorAll('.col')).toHaveLength(0)
    unmount()
    const curto = render(<ColunasMes a={pontos(12)} rotuloA="X" />)
    expect(curto.container.querySelector('.grafico-linha')).toBeNull()
    expect(curto.container.querySelectorAll('.col')).toHaveLength(12)
  })
})

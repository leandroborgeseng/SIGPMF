import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ColunasMes, FonteRodape, indicesSemSobreposicao, rotuloDado } from '../paineis/graficos'

const pontos = (n: number, base = 1_500_000) => Array.from({ length: n }, (_, i) => ({
  periodo: `20${String(10 + Math.floor(i / 12))}-${String((i % 12) + 1).padStart(2, '0')}`, dim: '', valor: base + i * 1_000_000,
}))

describe('rótulos, sobreposição e tendência', () => {
  it('abrevia mil, mi e bi em pt-BR', () => {
    expect(rotuloDado(1_500)).toBe('1,5 mil')
    expect(rotuloDado(2_500_000)).toBe('2,5 mi')
    expect(rotuloDado(3_000_000_000)).toBe('3 bi')
  })

  it('mostra rótulos visíveis, esconde os que sobrepõem e identifica a tendência', () => {
    const n = 36
    const { container } = render(<ColunasMes a={pontos(n)} rotuloA="Atendimentos" meses={72} fonte="SAUDE" atualizadoEm="2026-11-04T12:00:00Z" />)
    const rotulos = container.querySelectorAll('.grafico-rotulo')
    expect(rotulos.length).toBeGreaterThan(0)
    expect(rotulos.length).toBeLessThan(n)
    expect(rotulos.length).toBe(indicesSemSobreposicao(n).length)
    expect(container.textContent).toMatch(/mi|mil|bi/)
    expect(screen.getByText('Tendência')).toBeVisible()
    expect(container.querySelector('.grafico-tendencia')).not.toBeNull()
    expect(screen.getByRole('note').textContent).toBe('Fonte: Saúde · atualizado em 04/11/2026')
  })

  it('o rodapé traz o sistema e a data', () => {
    render(<FonteRodape fonte="FINANCEIRO" atualizadoEm="2026-03-15" />)
    expect(screen.getByRole('note').textContent).toBe('Fonte: Financeiro · atualizado em 15/03/2026')
  })
})

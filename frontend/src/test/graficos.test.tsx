import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ColunasMes, FonteRodape, caixasCruzam, indicesSemSobreposicao, reais, rotuloDado } from '../paineis/graficos'

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

  it('barras agrupadas não colidem, ficam acima da própria barra e têm halo branco', () => {
    const n = 12
    const a = Array.from({ length: n }, (_, i) => ({ periodo: `2024-${String(i + 1).padStart(2, '0')}`, dim: '', valor: 3_200_000 + i * 80_000 }))
    const b = Array.from({ length: n }, (_, i) => ({ periodo: `2024-${String(i + 1).padStart(2, '0')}`, dim: '', valor: 2_700_000 + i * 60_000 }))
    const { container } = render(<ColunasMes a={a} b={b} rotuloA="Lançado" rotuloB="Arrecadado" formato={reais} meses={12} />)
    const els = [...container.querySelectorAll('.grafico-rotulo')]
    expect(els.length).toBeGreaterThan(0)
    const caixas = els.map((el) => ({
      x: Number(el.getAttribute('data-x')), y: Number(el.getAttribute('data-y')),
      w: Number(el.getAttribute('data-w')), h: Number(el.getAttribute('data-h')),
      barra: Number(el.getAttribute('data-barra')),
    }))
    caixas.forEach((caixa, i) => {
      expect(caixa.y + caixa.h).toBeLessThanOrEqual(caixa.barra + 0.01)
      caixas.slice(i + 1).forEach((outra) => expect(caixasCruzam(caixa, outra, 0)).toBe(false))
    })
    expect(els.some((el) => el.getAttribute('data-serie') === 'a')).toBe(true)
    expect(els.some((el) => el.getAttribute('data-serie') === 'b')).toBe(true)
    els.forEach((el) => {
      expect(el.getAttribute('paint-order')).toBe('stroke')
      expect(el.getAttribute('stroke')?.toLowerCase()).toBe('#ffffff')
      expect(el.getAttribute('fill')).toBe('#1A2332')
    })
    const area = container.querySelector('.grafico-area')!
    const filhos = [...area.children]
    const tendencia = filhos.findIndex((n) => (n as Element).classList.contains('grafico-tendencia'))
    const rotulos = filhos.findIndex((n) => (n as Element).classList.contains('grafico-rotulos'))
    expect(tendencia).toBeGreaterThanOrEqual(0)
    expect(rotulos).toBeGreaterThan(tendencia)
  })

  it('o rodapé traz o sistema e a data', () => {
    render(<FonteRodape fonte="FINANCEIRO" atualizadoEm="2026-03-15" />)
    expect(screen.getByRole('note').textContent).toBe('Fonte: Financeiro · atualizado em 15/03/2026')
  })
})

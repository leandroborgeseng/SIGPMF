import { describe, expect, it } from 'vitest'
import { milhar, perda } from '../pages/PainelBases'

describe('K1: contagem null (cache frio) mostra "—", nunca 0', () => {
  it('milhar(null) é "—" e milhar(0) segue "0"', () => {
    expect(milhar(null)).toBe('—')
    expect(milhar(undefined)).toBe('—')
    expect(milhar(0)).toBe('0')
    expect(milhar(1200)).toBe('1.200')
  })
  it('perda com null não calcula', () => {
    expect(perda(null, 10)).toBeNull()
    expect(perda(100, null)).toBeNull()
  })
})

import { plural } from '../pages/PainelBases'

describe('plural (contagem de bases)', () => {
  it('singular só para 1', () => {
    expect(plural(1, 'pronta', 'prontas')).toBe('1 pronta')
    expect(plural(1, 'mapeada', 'mapeadas')).toBe('1 mapeada')
    expect(plural(1, 'triada', 'triadas')).toBe('1 triada')
    expect(plural(1, 'base cadastrada', 'bases cadastradas')).toBe('1 base cadastrada')
  })
  it('plural para 0 e ≥ 2, com milhar pt-BR', () => {
    expect(plural(0, 'descartada', 'descartadas')).toBe('0 descartadas')
    expect(plural(2, 'pronta', 'prontas')).toBe('2 prontas')
    expect(plural(2, 'mapeada', 'mapeadas')).toBe('2 mapeadas')
    expect(plural(1234, 'triada', 'triadas')).toBe('1.234 triadas')
  })
})

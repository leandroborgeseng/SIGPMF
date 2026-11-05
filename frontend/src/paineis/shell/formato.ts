import type { KpiDado, Variacao } from './tipos'

const f1 = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 })
const f0 = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 })

/** pt-BR: R$ 1,2 mi · 12,4% · 1.234 (UXD8). "—" quando não há valor. */
export function valorFmt(v: number | null, formato: KpiDado['formato']): string {
  if (v == null) return '—'
  if (formato === 'pct') return `${f1.format(v)}%`
  if (formato === 'n') return f0.format(v)
  const a = Math.abs(v)
  if (a >= 1e9) return `R$ ${f1.format(v / 1e9)} bi`
  if (a >= 1e6) return `R$ ${f1.format(v / 1e6)} mi`
  if (a >= 1e4) return `R$ ${f1.format(v / 1e3)} mil`
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(v)
}

const sinal = (v: number) => (v > 0 ? '+' : v < 0 ? '−' : '±')

/** Texto completo da variação: seta + sinal + texto (nunca só cor, UXD3). "—" quando depende de mês sem carga (P02). */
export function variacaoTexto(v: Variacao, formato: KpiDado['formato']): { seta: string; texto: string; leitura: string } {
  if (v.delta == null) return { seta: '', texto: '—', leitura: `sem comparação com ${v.referencia} (mês sem carga ou grupo oculto)` }
  const seta = v.sentido === 'alta' ? '▲' : v.sentido === 'queda' ? '▼' : '■'
  const abs = Math.abs(v.delta)
  const corpo = formato === 'pct' ? `${sinal(v.delta)}${f1.format(abs)} p.p.`
    : v.pct != null ? `${sinal(v.delta)}${f1.format(Math.abs(v.pct))}%` : `${sinal(v.delta)}${valorFmt(abs, formato)}`
  const palavra = v.sentido === 'alta' ? 'alta' : v.sentido === 'queda' ? 'queda' : 'estável'
  return { seta, texto: corpo, leitura: `${palavra} de ${corpo} em relação a ${v.referencia}` }
}

/** Tom semântico: depende de "alta é boa" do KPI; o texto já diz o sentido, a cor só reforça. */
export function tom(v: Variacao, altaBoa: boolean): 'bom' | 'ruim' | 'neutro' {
  if (v.delta == null || v.sentido === 'estavel') return 'neutro'
  return (v.sentido === 'alta') === altaBoa ? 'bom' : 'ruim'
}

/** Mesmo cálculo do backend (servico.calcula), para o drill-down mostrar o KPI escolhido sem nova consulta. */
export function calcula(c: KpiDado['calculo'], m1: number, m2: number, n: number): number | null {
  if (c === 'm1') return m1
  if (c === 'm2') return m2
  if (c === 'n') return n
  if (c === 'razao_m2_m1') return m1 ? Math.round((10000 * m2) / m1) / 100 : null
  return n ? Math.round((100 * m1) / n) / 100 : null
}

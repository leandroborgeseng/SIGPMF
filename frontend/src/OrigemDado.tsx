/** UXO1–UXO9 / EN5–EN8: procedência do dado num chip próprio, abaixo do valor (nunca na mesma linha). */
const NOMES: Record<string, string> = {
  SAUDE: 'Saúde', IPTU: 'IPTU', DIVIDA: 'Dívida Ativa', SIL: 'SIL (empresas)', EDUCACAO: 'Educação',
  CADUNICO: 'CadÚnico', FINANCEIRO: 'Financeiro',
}
const MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro',
  'novembro', 'dezembro']

/** UXO3: nome por extenso; fora do mapa, o código com a primeira letra maiúscula. */
export function nomeFonte(f: string | null | undefined): string {
  if (!f) return 'Fonte não informada'
  const k = f.split('.')[0].toUpperCase()
  return NOMES[k] ?? k.charAt(0) + k.slice(1).toLowerCase()
}

function data(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null
}
const dd = (d: Date) => `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`
const extenso = (d: Date) => `${d.getDate()} de ${MESES[d.getMonth()]} de ${d.getFullYear()}`

/** UXO4: "1 ano e 8 meses" (só anos/meses completos). */
export function idade(d: Date, agora: Date): { meses: number; texto: string } {
  let meses = (agora.getFullYear() - d.getFullYear()) * 12 + agora.getMonth() - d.getMonth()
  if (agora.getDate() < d.getDate()) meses -= 1
  const a = Math.floor(meses / 12), m = meses % 12
  const pa = a ? `${a} ${a === 1 ? 'ano' : 'anos'}` : ''
  const pm = m ? `${m} ${m === 1 ? 'mês' : 'meses'}` : ''
  return { meses, texto: [pa, pm].filter(Boolean).join(' e ') }
}

export default function OrigemDado({ fonte, atualizadoEm, agora = new Date() }: {
  fonte: string | null | undefined; atualizadoEm: string | null | undefined; agora?: Date
}) {
  const nome = nomeFonte(fonte)
  const d = data(atualizadoEm)
  const velho = d ? idade(d, agora) : null
  const antigo = velho !== null && velho.meses > 12
  const falado = d ? `Fonte: ${nome}, atualizado em ${extenso(d)}` : `Fonte: ${nome}`
  const textoVisivel = !d ? nome : antigo ? `${nome} · atualizado há ${velho!.texto}` : `${nome} · atualizado em ${dd(d)}`
  return (
    <span className={antigo ? 'origem-dado antigo' : 'origem-dado'} role="note" aria-label={falado}
      title={antigo ? `Atualizado em ${dd(d!)}` : undefined} tabIndex={antigo ? 0 : undefined}>
      {antigo && <span aria-hidden="true" className="origem-relogio">🕒 </span>}
      <span aria-hidden="true">{textoVisivel}</span>
    </span>
  )
}

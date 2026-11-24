import { useEffect, useState } from 'react'
import { api } from './api'
import OrigemDado from './OrigemDado'

type Indicador = { tem: boolean; fontes: string[]; ficha: string | null }
type PessoaNo = {
  nome: string | null; cpf: string | null; cidadao_id: string | null; nascimento: string | null
  indicadores: { imovel: Indicador; tumulo: Indicador; empresa: Indicador }
}
type Aresta = {
  base: string; forca: string; parentesco: string; confianca: number; criterio: string
  fonte_atualizado_em: string | null; provavel: boolean
}
type Parente = { nivel: number; via: string | null; pessoa: PessoaNo; arestas: Aresta[] }
type Arvore = {
  foco: PessoaNo; ascendentes: Parente[]; descendentes: Parente[]; laterais: Parente[]
  provedores: { codigo: string; disponivel: boolean; motivo: string | null }[]
  criterio_vinculo_fraco: string; isento_k_anonimato: boolean
}

const EXTENSAO: Record<string, string> = { CADUNICO: 'CadÚnico', BU_EDUCACAO: 'BU educação' }

function valida(x: unknown): x is Arvore {
  if (!x || typeof x !== 'object') return false
  const o = x as Partial<Arvore>
  return Array.isArray(o.ascendentes) && Array.isArray(o.descendentes) && !!o.foco && typeof o.foco === 'object'
}

function Selo({ a }: { a: Aresta }) {
  return a.provavel || a.forca === 'fraco'
    ? <span className="selo-provavel">provável · {Math.round(a.confianca * 100)}%</span>
    : <span className="selo-forte">forte</span>
}

function Ligacoes({ p }: { p: PessoaNo }) {
  const itens = [
    ['Imóvel', p.indicadores?.imovel],
    ['Túmulo', p.indicadores?.tumulo],
    ['Empresa', p.indicadores?.empresa],
  ] as const
  const tem = itens.filter(([, i]) => i?.tem)
  if (!tem.length) return null
  return (
    <p className="arvore-ligacoes">
      {tem.map(([rotulo, i]) => i?.ficha
        ? <a key={rotulo} href={i.ficha}>{rotulo}{i.fontes?.length ? ` (${i.fontes.join(', ')})` : ''}</a>
        : <span key={rotulo}>{rotulo}{i?.fontes?.length ? ` (${i.fontes.join(', ')})` : ''}</span>)}
    </p>
  )
}

function Cartao({ item }: { item: Parente }) {
  const p = item.pessoa
  return (
    <li className="arvore-cartao">
      <p className="arvore-nivel">{item.nivel > 0 ? `Nível ${item.nivel}` : 'Mesma geração'}{item.via ? ` · via ${item.via}` : ''}</p>
      <strong>{p.nome || 'Nome não informado'}</strong>
      {p.cpf && <span className="arvore-cpf">CPF {p.cpf}</span>}
      <Ligacoes p={p} />
      <ul className="arvore-arestas">
        {(item.arestas ?? []).map((a, i) => (
          <li key={`${a.base}-${a.parentesco}-${i}`}>
            <Selo a={a} />
            <span>{a.parentesco}</span>
            <OrigemDado fonte={a.base} atualizadoEm={a.fonte_atualizado_em} />
            {a.provavel && <span className="arvore-criterio">{a.criterio}</span>}
          </li>
        ))}
      </ul>
    </li>
  )
}

function Coluna({ titulo, itens, vazio }: { titulo: string; itens: Parente[]; vazio: string }) {
  return (
    <div>
      <h4>{titulo}</h4>
      {itens.length === 0 ? <p className="area-vazia">{vazio}</p> : (
        <ul className="arvore-lista">{itens.map((item, i) => <Cartao key={`${item.nivel}-${item.pessoa.cidadao_id ?? ''}-${i}`} item={item} />)}</ul>
      )}
    </div>
  )
}

export default function ArvoreGenealogica({ id }: { id: string }) {
  const [arv, setArv] = useState<Arvore | null>(null)
  const [estado, setEstado] = useState<'carregando' | 'ok' | 'indisponivel'>('carregando')
  useEffect(() => {
    let vivo = true
    setEstado('carregando'); setArv(null)
    api<unknown>(`/gold/cidadaos/${id}/genealogia`).then((r) => {
      if (!vivo) return
      if (valida(r)) { setArv(r); setEstado('ok') } else setEstado('indisponivel')
    }).catch(() => { if (vivo) setEstado('indisponivel') })
    return () => { vivo = false }
  }, [id])
  const fora = (arv?.provedores ?? []).filter((p) => !p.disponivel)
  return (
    <section className="cartao portal-bloco arvore" data-testid="arvore-genealogica" aria-labelledby="t-arvore">
      <h3 id="t-arvore">Árvore genealógica</h3>
      <p className="sutil">Ascendentes e descendentes, até dois níveis. O vínculo forte vem de CPF na origem. O vínculo por nome de mãe ou pai aparece como provável, com o nível de confiança.</p>
      {estado === 'carregando' && <p>Carregando a árvore genealógica…</p>}
      {estado === 'indisponivel' && <p>Não foi possível montar a árvore nesta leitura.</p>}
      {estado === 'ok' && arv && (
        <>
          <div className="arvore-grade">
            <Coluna titulo="Ascendentes" itens={arv.ascendentes} vazio="Nenhum ascendente encontrado nas bases carregadas." />
            <Coluna titulo="Descendentes" itens={arv.descendentes} vazio="Nenhum descendente encontrado nas bases carregadas." />
          </div>
          {arv.laterais.length > 0 && (
            <div className="arvore-laterais">
              <Coluna titulo="Cônjuge" itens={arv.laterais} vazio="" />
            </div>
          )}
          <details className="arvore-criterio-completo">
            <summary>Critério da confiança do vínculo provável</summary>
            <p>{arv.criterio_vinculo_fraco}</p>
          </details>
          {fora.length > 0 && (
            <p className="sutil">Sem acesso, com ponto de extensão para plugar depois: {fora.map((p) => EXTENSAO[p.codigo] ?? p.codigo).join(' e ')}.</p>
          )}
        </>
      )}
    </section>
  )
}

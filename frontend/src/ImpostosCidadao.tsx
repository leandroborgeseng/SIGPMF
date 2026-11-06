import { useEffect, useState } from 'react'
import { api, dataBR } from './api'
import { Area, estadoArea, type Fontes, type Leitura } from './AreaFicha'

type Totais = { imoveis: number; em_aberto: number; parcelas_em_aberto: number; vencido: number; parcelas_vencidas: number
  divida_ativa: number; inscricoes_divida: number; protestada: boolean; lancamentos?: number }
type ImovelT = { codi: string; inscricao: string | null; numero: string | null; complemento: string | null; cd_bairro: number | null
  iptu_anual: number | null; tipo_posse: number | null; desde: string | null; ate: string | null; ativo: boolean }
type LancT = { exercicio: number; tributo: string | null; parcela: number | null; codi: string | null; vencimento: string | null
  valor: number | null; valor_pago: number | null; pagamento: string | null; pago: boolean; situacao: string }
type DivT = { codi: string; sequ?: string | null; ano: number | null; natureza: number | null; natureza_nome?: string | null; origem?: string | null; inscrita_em: string | null
  saldo: number | null; saldo_bruto?: number | null; pago?: number | null; situacao?: string | null; em_aberto?: boolean
  valor_origem: number | null; protestos: number; extinta: boolean }
type EmpresaT = { ccm: number; nome: string | null; documento: string; possui_debito: boolean | null; endereco: string | null
  bairro: string | null }
export type RespTributario = { cidadao_id: string; totais: Totais; imoveis: ImovelT[]; lancamentos: LancT[]; dividas: DivT[]
  empresas?: EmpresaT[] }

export const validaTributario = (r: unknown): r is RespTributario => !!r && typeof (r as RespTributario).totais === 'object'
  && Array.isArray((r as RespTributario).imoveis) && Array.isArray((r as RespTributario).lancamentos) && Array.isArray((r as RespTributario).dividas)
export const reais = (v: number | null | undefined) => v == null ? '—'
  : new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v)
const SITUACAO: Record<string, [string, string]> = { pago: ['sucesso', 'Pago'], vencido: ['falha', 'Vencido'], a_vencer: ['em_andamento', 'A vencer'] }

/** TR7: bloco "Impostos e imóveis" da ficha (prata tributária). Consulta auditada no backend. */
export default function ImpostosCidadao({ id, dados, leitura, fontes }: { id: string; dados?: RespTributario | null | false; leitura?: Leitura; fontes?: Fontes | null | false }) {
  const [buscado, setD] = useState<RespTributario | null>(null)
  const [erro, setFalhou] = useState(false)
  const [todos, setTodos] = useState(false)
  const externo = dados !== undefined  // a ficha já buscou: não busca de novo (nem registra a consulta duas vezes)
  useEffect(() => {
    if (externo) return
    setD(null); setFalhou(false)
    api<unknown>(`/gold/cidadaos/${encodeURIComponent(id)}/tributario`)
      .then((r) => (validaTributario(r) ? setD(r) : setFalhou(true))).catch(() => setFalhou(true))
  }, [id, externo])
  const d = externo ? (dados || null) : buscado
  const falhou = externo ? dados === false : erro

  const l: Leitura = leitura ?? (falhou ? 'falha' : !d ? 'carregando' : 'ok')
  const empresas = d?.empresas ?? []
  const t = d?.totais
  const totalLancs = t?.lancamentos ?? d?.lancamentos.length ?? 0
  const lancs = d?.lancamentos ?? []
  return (
    <section className="cartao impostos" aria-labelledby="t-impostos">
      <h3 id="t-impostos">Impostos e imóveis</h3>
      {t && (
        <div className="impostos-totais">
          <div className="cartao"><span className="cartao-rotulo">Em aberto</span><span className="cartao-valor">{reais(t.em_aberto)}</span>
            <span className="sutil">{t.parcelas_em_aberto} parcela(s){t.parcelas_vencidas ? ` · ${t.parcelas_vencidas} vencida(s)` : ''}</span></div>
          <div className="cartao"><span className="cartao-rotulo">Dívida ativa</span><span className={`cartao-valor${t.divida_ativa > 0 ? ' perda' : ''}`}>{reais(t.divida_ativa)}</span>
            <span className="sutil">{t.inscricoes_divida} inscrição(ões){t.protestada ? ' · protestada' : ''}</span></div>
        </div>
      )}
      <Area id="imoveis" titulo="Imóveis" estado={estadoArea(l, d?.imoveis.length ?? 0, fontes, 'imoveis')} total={d?.imoveis.length} vazio="Nenhum imóvel ligado a este cidadão.">
        <ul className="impostos-imoveis" aria-label="Imóveis">
          {d?.imoveis.map((i) => (
            <li key={`${i.codi}-${i.desde}`} className={i.ativo ? '' : 'inativo'}>
              <div>
                <strong>{i.complemento || 'Imóvel'}{i.numero ? `, ${i.numero}` : ''}</strong>
                <span className="sutil"> · Inscrição {i.inscricao ?? i.codi} · {i.ativo ? `desde ${dataBR(i.desde)}` : `até ${dataBR(i.ate)}`}</span>
              </div>
              <span className={`selo ${i.ativo ? 'sucesso' : 'neutro'}`}><i aria-hidden="true" />{i.ativo ? 'Posse atual' : 'Posse anterior'}</span>
            </li>
          ))}
        </ul>
      </Area>
      <Area id="lancamentos" titulo="Impostos e taxas lançados" estado={estadoArea(l, totalLancs, fontes, 'lancamentos')} total={totalLancs} vazio="Nenhum imposto ou taxa lançado para este cidadão.">
        <table className="tabela">
          <caption className="sr-only">Lançamentos</caption>
          <thead><tr><th scope="col">Exercício</th><th scope="col">Parcela</th><th scope="col">Vencimento</th><th scope="col">Valor</th><th scope="col">Situação</th></tr></thead>
          <tbody>
            {(todos ? lancs : lancs.slice(0, 8)).map((x) => {
              const [cls, txt] = SITUACAO[x.situacao] ?? ['neutro', x.situacao]
              return (
                <tr key={`${x.exercicio}-${x.tributo}-${x.parcela}-${x.codi}-${x.vencimento}`}>
                  <td data-rotulo="Exercício">{x.exercicio}</td><td data-rotulo="Parcela">{x.parcela ?? '—'}</td>
                  <td data-rotulo="Vencimento">{dataBR(x.vencimento)}</td><td data-rotulo="Valor">{reais(x.valor)}</td>
                  <td data-rotulo="Situação"><span className={`selo ${cls}`}><i aria-hidden="true" />{txt}</span></td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {lancs.length > 8 && <button className="btn-link" aria-expanded={todos} onClick={() => setTodos(!todos)}>{todos ? 'Mostrar menos' : `Ver todos (${lancs.length})`}</button>}
        {totalLancs > lancs.length && todos && <p className="sutil">Mostrando os {lancs.length} mais recentes de {totalLancs}. Os totais acima contam todos.</p>}
      </Area>
      <Area id="dividas" titulo="Dívida ativa" estado={estadoArea(l, d?.dividas.length ?? 0, fontes, 'dividas')} total={d?.dividas.length} vazio="Nenhuma dívida ativa para este cidadão.">
        <ul className="impostos-imoveis" aria-label="Dívida ativa">
          {d?.dividas.map((x) => (
            <li key={`${x.codi}-${x.sequ ?? ''}-${x.ano}-${x.situacao ?? ''}`} className={x.em_aberto ? '' : 'inativo'}>
              <div><strong>Dívida {x.ano ?? ''}</strong><span className="sutil">{x.origem ? ` · ${x.origem}` : ''}{x.natureza_nome ? ` · ${x.natureza_nome}` : ''} · inscrita em {dataBR(x.inscrita_em)}{x.protestos ? ` · ${x.protestos} protesto(s)` : ''}</span></div>
              <span className={`selo ${x.em_aberto ? 'falha' : 'neutro'}`}><i aria-hidden="true" />{x.extinta ? 'Extinta' : x.em_aberto ? reais(x.saldo) : 'Sem saldo em aberto'}</span>
            </li>
          ))}
        </ul>
      </Area>
      <Area id="empresas" titulo="Empresas no CPF" estado={estadoArea(l, empresas.length, fontes, 'empresas')} total={empresas.length} vazio="Nenhuma empresa com inscrição municipal no CPF deste cidadão.">
        <ul className="impostos-imoveis" aria-label="Empresas">
          {empresas.map((e) => (
            <li key={e.ccm}>
              <div>
                <strong>{e.nome || 'Empresa sem nome'}</strong>
                <span className="sutil"> · CCM {e.ccm}{e.bairro ? ` · ${e.bairro}` : ''}</span>
              </div>
              <span className={`selo ${e.possui_debito ? 'falha' : e.possui_debito === false ? 'sucesso' : 'neutro'}`}>
                <i aria-hidden="true" />{e.possui_debito ? 'Com débito' : e.possui_debito === false ? 'Sem débito' : 'Sem informação'}
              </span>
            </li>
          ))}
        </ul>
      </Area>
    </section>
  )
}

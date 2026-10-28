import { useEffect, useState } from 'react'
import { api, dataBR } from './api'

type Totais = { imoveis: number; em_aberto: number; parcelas_em_aberto: number; vencido: number; parcelas_vencidas: number
  divida_ativa: number; inscricoes_divida: number; protestada: boolean }
type ImovelT = { codi: string; inscricao: string | null; numero: string | null; complemento: string | null; cd_bairro: number | null
  iptu_anual: number | null; tipo_posse: number | null; desde: string | null; ate: string | null; ativo: boolean }
type LancT = { exercicio: number; tributo: string | null; parcela: number | null; codi: string | null; vencimento: string | null
  valor: number | null; valor_pago: number | null; pagamento: string | null; pago: boolean; situacao: string }
type DivT = { codi: string; ano: number | null; natureza: number | null; inscricao: string | null; saldo: number | null
  valor_origem: number | null; protestos: number; extinta: boolean }
export type RespTributario = { cidadao_id: string; totais: Totais; imoveis: ImovelT[]; lancamentos: LancT[]; dividas: DivT[] }

export const validaTributario = (r: unknown): r is RespTributario => !!r && typeof (r as RespTributario).totais === 'object'
  && Array.isArray((r as RespTributario).imoveis) && Array.isArray((r as RespTributario).lancamentos) && Array.isArray((r as RespTributario).dividas)
export const reais = (v: number | null | undefined) => v == null ? '—'
  : new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v)
const SITUACAO: Record<string, [string, string]> = { pago: ['sucesso', 'Pago'], vencido: ['falha', 'Vencido'], a_vencer: ['em_andamento', 'A vencer'] }

/** TR7: bloco "Impostos e imóveis" da ficha (prata tributária). Consulta auditada no backend. */
export default function ImpostosCidadao({ id, dados }: { id: string; dados?: RespTributario | null | false }) {
  const [buscado, setD] = useState<RespTributario | null>(null)
  const [erro, setFalhou] = useState(false)
  const externo = dados !== undefined  // a ficha já buscou: não busca de novo (nem registra a consulta duas vezes)
  useEffect(() => {
    if (externo) return
    setD(null); setFalhou(false)
    api<unknown>(`/gold/cidadaos/${encodeURIComponent(id)}/tributario`)
      .then((r) => (validaTributario(r) ? setD(r) : setFalhou(true))).catch(() => setFalhou(true))
  }, [id, externo])
  const d = externo ? (dados || null) : buscado
  const falhou = externo ? dados === false : erro

  if (falhou) return <section className="cartao impostos" aria-labelledby="t-impostos"><h3 id="t-impostos">Impostos e imóveis</h3><p className="sutil">Sem leitura dos dados tributários.</p></section>
  if (!d) return <section className="cartao impostos" aria-labelledby="t-impostos"><h3 id="t-impostos">Impostos e imóveis</h3><p className="sutil">Carregando…</p></section>
  const t = d.totais
  const vazio = d.imoveis.length === 0 && d.lancamentos.length === 0 && d.dividas.length === 0
  return (
    <section className="cartao impostos" aria-labelledby="t-impostos">
      <h3 id="t-impostos">Impostos e imóveis</h3>
      {vazio ? <p className="sutil">Nenhum imóvel, lançamento ou dívida ligado a este cidadão.</p> : (
        <>
          <div className="impostos-totais">
            <div className="cartao"><span className="cartao-rotulo">Imóveis</span><span className="cartao-valor">{t.imoveis}</span></div>
            <div className="cartao"><span className="cartao-rotulo">Em aberto</span><span className="cartao-valor">{reais(t.em_aberto)}</span>
              <span className="sutil">{t.parcelas_em_aberto} parcela(s){t.parcelas_vencidas ? ` · ${t.parcelas_vencidas} vencida(s)` : ''}</span></div>
            <div className="cartao"><span className="cartao-rotulo">Dívida ativa</span><span className={`cartao-valor${t.divida_ativa > 0 ? ' perda' : ''}`}>{reais(t.divida_ativa)}</span>
              <span className="sutil">{t.inscricoes_divida} inscrição(ões){t.protestada ? ' · protestada' : ''}</span></div>
          </div>
          {d.imoveis.length > 0 && (
            <ul className="impostos-imoveis" aria-label="Imóveis">
              {d.imoveis.map((i) => (
                <li key={`${i.codi}-${i.desde}`} className={i.ativo ? '' : 'inativo'}>
                  <div>
                    <strong>{i.complemento || 'Imóvel'}{i.numero ? `, ${i.numero}` : ''}</strong>
                    <span className="sutil"> · Inscrição {i.inscricao ?? i.codi} · {i.ativo ? `desde ${dataBR(i.desde)}` : `até ${dataBR(i.ate)}`}</span>
                  </div>
                  <span className={`selo ${i.ativo ? 'sucesso' : 'neutro'}`}><i aria-hidden="true" />{i.ativo ? 'Posse atual' : 'Posse anterior'}</span>
                </li>
              ))}
            </ul>
          )}
          {d.lancamentos.length > 0 && (
            <table className="tabela">
              <caption className="sr-only">Lançamentos</caption>
              <thead><tr><th scope="col">Exercício</th><th scope="col">Parcela</th><th scope="col">Vencimento</th><th scope="col">Valor</th><th scope="col">Situação</th></tr></thead>
              <tbody>
                {d.lancamentos.map((x) => {
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
          )}
          {d.dividas.length > 0 && (
            <ul className="impostos-imoveis" aria-label="Dívida ativa">
              {d.dividas.map((x) => (
                <li key={`${x.codi}-${x.ano}`} className={x.extinta ? 'inativo' : ''}>
                  <div><strong>Dívida {x.ano ?? ''}</strong><span className="sutil"> · inscrita em {dataBR(x.inscricao)}{x.protestos ? ` · ${x.protestos} protesto(s)` : ''}</span></div>
                  <span className={`selo ${x.extinta ? 'neutro' : 'falha'}`}><i aria-hidden="true" />{x.extinta ? 'Extinta' : reais(x.saldo)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

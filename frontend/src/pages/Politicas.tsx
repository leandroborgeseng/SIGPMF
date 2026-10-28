import { useCallback, useEffect, useState } from 'react'
import { api, ApiErro, dataHoraBR } from '../api'
import { useAuth } from '../auth'
import { Erro, Esqueleto } from '../componentes'

type Regra = { camada: string; titulo: string; texto: string }
type Conjunto = { nome: string; origem: string; classe: string; base: string; retencao: string; tratamento: string; quem: string }
type Resp = {
  regras: Regra[]; conjuntos: Conjunto[]; areas: string[]; perfis: { perfil: string; celulas: string[] }[]
  retencao: { bronze_versoes_dias: number; logs_dias: number; ultima: { status: string; iniciado_em: string; mensagem: string | null; apagados: number } | null }
}
const CLASSE: Record<string, [string, string]> = { publico: ['neutro', 'Público'], interno: ['mapeado', 'Interno'], pessoal: ['atencao', 'Pessoal'], sensivel: ['falha', 'Sensível'] }
const CELULA: Record<string, [string, string]> = { ver: ['sucesso', 'Vê'], total: ['mapeado', 'Totais'], nada: ['neutro', '—'] }
const valida = (r: unknown): r is Resp => !!r && Array.isArray((r as Resp).regras) && Array.isArray((r as Resp).conjuntos) && Array.isArray((r as Resp).perfis)

/** PL4: políticas de dados (versionadas no código) e a limpeza automática. */
export default function Politicas() {
  const { sessao } = useAuth()
  const [d, setD] = useState<Resp | null>(null)
  const [falhou, setFalhou] = useState(false)
  const [aviso, setAviso] = useState('')
  const [rodando, setRodando] = useState(false)
  const carregar = useCallback(() => {
    setFalhou(false); setD(null)
    api<unknown>('/politicas').then((r) => (valida(r) ? setD(r) : setFalhou(true))).catch(() => setFalhou(true))
  }, [])
  useEffect(carregar, [carregar])
  const executar = async () => {
    setRodando(true); setAviso('')
    try {
      const r = await api<{ mensagem: string }>('/politicas/retencao/executar', { method: 'POST' })
      setAviso(r.mensagem); carregar()
    } catch (e) {
      setAviso(e instanceof ApiErro ? e.message : 'Não foi possível executar a limpeza.')
    } finally {
      setRodando(false)
    }
  }
  return (
    <section aria-labelledby="t-pol" className="painel">
      <header className="page-head">
        <p className="kicker">Dados</p>
        <h1 id="t-pol">Políticas de dados</h1>
        <p className="sutil">Classificação, base legal, retenção, tratamento e acesso de cada conjunto de dados.</p>
      </header>
      <p className="aviso-ambar">Prazos e bases legais são a proposta técnica: a palavra final é do encarregado (DPO) e da Procuradoria.</p>
      {falhou ? <Erro texto="Não foi possível carregar as políticas." onRetry={carregar} /> : !d ? <Esqueleto linhas={4} /> : (
        <>
          <div className="kpis">
            {d.regras.map((r) => (
              <div key={r.camada} className="cartao pol-regra">
                <span className="kicker">{r.camada}</span>
                <strong>{r.titulo}</strong>
                <span className="sutil">{r.texto}</span>
              </div>
            ))}
          </div>
          <section className="cartao painel-bloco" aria-labelledby="t-conj">
            <div className="painel-bloco-cab"><h2 id="t-conj">Conjuntos de dados</h2></div>
            <div className="pol-rolagem">
              <table className="tabela pol-tabela">
                <caption className="sr-only">Conjuntos de dados e suas políticas</caption>
                <thead><tr><th scope="col">Conjunto</th><th scope="col">Classificação</th><th scope="col">Finalidade e base legal</th><th scope="col">Retenção (bronze / prata / ouro)</th><th scope="col">Tratamento</th><th scope="col">Quem vê o dado individual</th></tr></thead>
                <tbody>
                  {d.conjuntos.map((c) => {
                    const [cls, txt] = CLASSE[c.classe] ?? ['neutro', c.classe]
                    return (
                      <tr key={c.nome}>
                        <th scope="row" data-rotulo="Conjunto"><strong>{c.nome}</strong><span className="sutil"> · {c.origem}</span></th>
                        <td data-rotulo="Classificação"><span className={`selo ${cls}`}><i aria-hidden="true" />{txt}</span></td>
                        <td data-rotulo="Base legal">{c.base}</td>
                        <td data-rotulo="Retenção">{c.retencao}</td>
                        <td data-rotulo="Tratamento">{c.tratamento}</td>
                        <td data-rotulo="Quem vê">{c.quem}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
          <div className="painel-grade">
            <section className="cartao painel-bloco" aria-labelledby="t-perfis">
              <div className="painel-bloco-cab"><h2 id="t-perfis">Perfis × áreas</h2></div>
              <div className="pol-rolagem">
                <table className="tabela pol-matriz">
                  <caption className="sr-only">Acesso por perfil e área</caption>
                  <thead><tr><th scope="col">Perfil</th>{d.areas.map((a) => <th key={a} scope="col">{a}</th>)}</tr></thead>
                  <tbody>
                    {d.perfis.map((p) => (
                      <tr key={p.perfil}>
                        <th scope="row" data-rotulo="Perfil">{p.perfil}</th>
                        {p.celulas.map((c, i) => {
                          const [cls, txt] = CELULA[c] ?? ['neutro', c]
                          return <td key={d.areas[i]} data-rotulo={d.areas[i]}><span className={`selo ${cls}`}>{txt}</span></td>
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="nota">Vê = ficha individual (registrada na auditoria) · Totais = só números agregados · — = sem acesso</p>
            </section>
            <section className="cartao painel-bloco" aria-labelledby="t-ret">
              <div className="painel-bloco-cab"><h2 id="t-ret">Limpeza automática</h2></div>
              <p className="sutil">Todo dia, depois das cargas da agenda: versões antigas da bronze com mais de {d.retencao.bronze_versoes_dias} dias e logs técnicos com mais de {d.retencao.logs_dias} dias. A versão atual de cada registro e a auditoria nunca são apagadas.</p>
              {d.retencao.ultima
                ? <p><span className={`selo ${d.retencao.ultima.status === 'sucesso' ? 'sucesso' : 'falha'}`}><i aria-hidden="true" />{d.retencao.ultima.status === 'sucesso' ? 'Última execução ok' : 'Última execução falhou'}</span>{' '}<span className="sutil">{dataHoraBR(d.retencao.ultima.iniciado_em)} · {d.retencao.ultima.mensagem}</span></p>
                : <p className="sutil">Ainda não executada.</p>}
              {sessao?.perfil === 'admin' && <button className="btn secundario" onClick={executar} disabled={rodando}>Executar agora</button>}
              <div aria-live="polite">{aviso && <p className="aviso">{aviso}</p>}</div>
            </section>
          </div>
        </>
      )}
    </section>
  )
}

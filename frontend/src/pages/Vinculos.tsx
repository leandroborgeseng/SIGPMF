import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiErro } from '../api'

type Flag = { texto: string; tom: string }
type No = { id: string; tipo: string; nome: string; doc: string; flags: Flag[]; cidadao_id: string | null }
type Vizinho = No & { relacao: string; fonte: string; grupo: string }
type Coluna = { no: No; vizinhos: Vizinho[] }
type Item = { cidadao_id: string; nome: string | null; cpf: string | null }

const MOTIVO_MIN = Number(import.meta.env.VITE_REVELAR_MOTIVO_MIN ?? 10)
const NIVEL_MAX = 3
const TIPO: Record<string, string> = { PF: 'PF', PJ: 'PJ', IM: 'IM', EM: 'EM' }

/** VN5: explorador de vínculos em colunas — cada clique abre só o próximo nível (consulta auditada com o motivo). */
export default function Vinculos() {
  const [motivo, setMotivo] = useState('')
  const [processo, setProcesso] = useState('')
  const [busca, setBusca] = useState('')
  const [achados, setAchados] = useState<Item[] | null>(null)
  const [colunas, setColunas] = useState<Coluna[]>([])
  const [erro, setErro] = useState('')
  const [carregando, setCarregando] = useState(false)
  const motivoOk = motivo.trim().length >= MOTIVO_MIN

  const abrir = async (noId: string, nivel: number) => {
    setErro(''); setCarregando(true)
    try {
      const q = new URLSearchParams({ motivo: motivo.trim(), processo: processo.trim(), nivel: String(nivel) })
      const r = await api<Coluna>(`/vinculos/${encodeURIComponent(noId)}?${q}`)
      setColunas((cs) => [...cs.slice(0, nivel), r])
    } catch (e) {
      setErro(e instanceof ApiErro ? e.message : 'Não foi possível abrir este vínculo.')
    } finally {
      setCarregando(false)
    }
  }
  const buscar = async (e: FormEvent) => {
    e.preventDefault(); setErro(''); setColunas([])
    try { setAchados((await api<Item[] | null>(`/silver/cidadaos?busca=${encodeURIComponent(busca)}`)) ?? []) }
    catch { setErro('Não foi possível buscar.') }
  }
  const sel = colunas[colunas.length - 1]?.no
  const proximoNoCaminho = (k: number) => colunas[k + 1]?.no.id

  return (
    <section aria-labelledby="t-vinc" className="vinculos">
      <header className="page-head">
        <p className="kicker">Dados · uso interno</p>
        <h1 id="t-vinc">Explorador de vínculos</h1>
        <p className="sutil">Pessoa → imóveis e empresas → outros possuidores → os imóveis deles. Cada abertura fica registrada com o motivo.</p>
      </header>
      <form className="cartao vinc-form" onSubmit={buscar}>
        <label htmlFor="v-motivo">Motivo da consulta (obrigatório)</label>
        <input id="v-motivo" value={motivo} onChange={(e) => setMotivo(e.target.value)} placeholder="Ex.: cobrança de dívida ativa" aria-describedby="v-motivo-ajuda" />
        <small id="v-motivo-ajuda" className="sutil">Mínimo de {MOTIVO_MIN} caracteres.</small>
        <label htmlFor="v-proc">Nº do processo (opcional)</label>
        <input id="v-proc" value={processo} onChange={(e) => setProcesso(e.target.value)} />
        <label htmlFor="v-busca">Pessoa (nome ou CPF completo)</label>
        <input id="v-busca" value={busca} onChange={(e) => setBusca(e.target.value)} />
        <button className="btn" type="submit" disabled={!motivoOk || !busca.trim()}>Buscar</button>
      </form>
      <div aria-live="assertive">{erro && <p className="erro-texto">{erro}</p>}</div>
      {achados && colunas.length === 0 && (
        achados.length === 0 ? <p className="vazio">Ninguém encontrado para "{busca}".</p> : (
          <ul className="lista-cartoes" aria-label="Pessoas encontradas">
            {achados.map((a) => (
              <li key={a.cidadao_id} className="cartao">
                <button className="link-btn" disabled={!motivoOk || carregando} onClick={() => abrir(`pf:${a.cidadao_id}`, 0)}>{a.nome || 'Nome não informado'}</button>
                <div className="sutil">CPF: {a.cpf ?? 'não informado'}</div>
              </li>
            ))}
          </ul>
        )
      )}
      {colunas.length > 0 && (
        <>
          <nav className="vinc-caminho" aria-label="Caminho percorrido">
            {colunas.map((c, k) => (
              <span key={c.no.id} className="vinc-passo">
                {k > 0 && <span className="vinc-seta" aria-hidden="true">›</span>}
                <button className={`chip${k === colunas.length - 1 ? ' ativo' : ''}`} onClick={() => setColunas((cs) => cs.slice(0, k + 1))}>
                  <span className={`vinc-tipo t-${c.no.tipo}`}>{TIPO[c.no.tipo] ?? c.no.tipo}</span>{c.no.nome}
                </button>
              </span>
            ))}
            <button className="btn secundario" onClick={() => setColunas([])}>Recomeçar</button>
          </nav>
          <div className="vinc-area">
            <div className="vinc-colunas" aria-label="Vínculos por nível">
              {colunas.map((c, k) => (
                <section key={c.no.id} className="vinc-coluna" aria-label={`Ligados a ${c.no.nome}`}>
                  <header><span className="sutil">Ligados a</span><strong>{c.no.nome}</strong></header>
                  {c.vizinhos.length === 0 ? <p className="sutil vinc-vazio">Nenhum vínculo nas bases integradas.</p> : (
                    <ul>
                      {c.vizinhos.map((v, i) => {
                        const novoGrupo = i === 0 || c.vizinhos[i - 1].grupo !== v.grupo
                        const ativo = proximoNoCaminho(k) === v.id
                        const limite = k + 1 > NIVEL_MAX
                        return (
                          <li key={`${v.id}-${i}`}>
                            {novoGrupo && <span className="vinc-grupo">{v.grupo}</span>}
                            <button className={`vinc-item${ativo ? ' ativo' : ''}`} disabled={carregando || limite}
                              title={limite ? `Limite de ${NIVEL_MAX} níveis` : undefined} onClick={() => abrir(v.id, k + 1)}>
                              <span className={`vinc-tipo t-${v.tipo}`}>{TIPO[v.tipo] ?? v.tipo}</span>
                              <span className="vinc-texto">
                                <strong>{v.nome}</strong>
                                <span>{v.relacao}</span>
                                <span className="sutil">fonte: {v.fonte} · {v.doc}</span>
                                {v.flags.length > 0 && <span className="vinc-flags">{v.flags.map((f) => <span key={f.texto} className={`selo ${f.tom === 'alerta' ? 'falha' : 'neutro'}`}><i aria-hidden="true" />{f.texto}</span>)}</span>}
                              </span>
                              <span aria-hidden="true" className="vinc-abre">›</span>
                            </button>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </section>
              ))}
            </div>
            {sel && (
              <aside className="cartao vinc-resumo" aria-label="Selecionado">
                <span className={`vinc-tipo t-${sel.tipo}`}>{TIPO[sel.tipo] ?? sel.tipo}</span>
                <h2>{sel.nome}</h2>
                <p className="sutil">{sel.doc}</p>
                <div className="vinc-flags">{sel.flags.map((f) => <span key={f.texto} className={`selo ${f.tom === 'alerta' ? 'falha' : 'neutro'}`}><i aria-hidden="true" />{f.texto}</span>)}</div>
                {sel.cidadao_id && <Link className="btn" to={`/simulador?cid=${encodeURIComponent(sel.cidadao_id)}`}>Abrir ficha completa</Link>}
                <p className="nota">Consulta registrada: {motivo.trim()}{processo.trim() ? ` · ${processo.trim()}` : ''} · {colunas.length} cadastro(s) aberto(s). Limite de {NIVEL_MAX} níveis.</p>
              </aside>
            )}
          </div>
        </>
      )}
    </section>
  )
}

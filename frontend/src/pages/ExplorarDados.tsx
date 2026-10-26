import { useCallback, useEffect, useState } from 'react'
import { ApiErro, dataHoraBR } from '../api'
import { useAuth } from '../auth'
import { Erro, Esqueleto } from '../componentes'
import {
  type Amostra, CAMADAS, type Camada, type Funil, type Linhagem, PERFIS_EXPLORADOR, type Perfil, type Tabelas, explorar, texto,
} from '../explorar'

import '../explorar.css'

const MOTIVO_MIN = 10

export default function ExplorarDados() {
  const { sessao } = useAuth()
  const [conectores, setConectores] = useState<string[] | null>(null)
  const [podeRevelar, setPodeRevelar] = useState(false)
  const [conector, setConector] = useState('')
  const [camada, setCamada] = useState<Camada>('bronze')
  const [erro, setErro] = useState<string | null>(null)

  const carregar = useCallback(async () => {
    setErro(null)
    try {
      const r = await explorar<{ conectores: { conector: string }[]; pode_revelar: boolean }>('conectores')
      setConectores(r.conectores.map((c) => c.conector))
      setPodeRevelar(r.pode_revelar)
      setConector((atual) => atual || r.conectores[0]?.conector || '')
    } catch (e) {
      setErro(e instanceof ApiErro && e.status === 403 ? 'Seu perfil não tem acesso ao explorador de dados.' : 'Não foi possível carregar os conectores.')
    }
  }, [])
  useEffect(() => { if (sessao && PERFIS_EXPLORADOR.includes(sessao.perfil)) void carregar() }, [carregar, sessao])

  if (!sessao || !PERFIS_EXPLORADOR.includes(sessao.perfil)) {
    return <section><h1>Explorar dados</h1><p role="alert">Seu perfil não tem acesso ao explorador de dados.</p></section>
  }
  return (
    <section className="explorar">
      <h1>Explorar dados</h1>
      <p className="sub">Somente leitura. Dados pessoais aparecem mascarados{podeRevelar ? '; para revelar um valor, informe o motivo (fica na auditoria).' : '.'}</p>
      {erro && <Erro texto={erro} onRetry={carregar} />}
      {!conectores && !erro && <Esqueleto texto="Carregando conectores…" />}
      {conectores && conectores.length === 0 && <p>Nenhum conector com dados carregados ainda.</p>}
      {conectores && conectores.length > 0 && (
        <>
          <label className="campo">Conector{' '}
            <select value={conector} onChange={(e) => setConector(e.target.value)}>
              {conectores.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          {conector && <FunilQualidade conector={conector} />}
          <div role="tablist" aria-label="Camada" className="abas">
            {CAMADAS.map((c) => (
              <button key={c.id} role="tab" aria-selected={camada === c.id} className={`aba${camada === c.id ? ' ativa' : ''}`}
                onClick={() => setCamada(c.id)}>{c.rotulo}</button>
            ))}
          </div>
          {conector && <CamadaPainel key={`${conector}:${camada}`} conector={conector} camada={camada} podeRevelar={podeRevelar} />}
        </>
      )}
    </section>
  )
}

function FunilQualidade({ conector }: { conector: string }) {
  const [f, setF] = useState<Funil | null>(null)
  useEffect(() => { setF(null); explorar<Funil>('funil', { conector }).then(setF).catch(() => setF(null)) }, [conector])
  if (!f) return null
  return (
    <div className="cartao funil" aria-label="Funil de qualidade">
      <h2>Funil Bronze → Prata → Ouro</h2>
      {f.estado.processando && <p role="status">{f.estado.mensagem}</p>}
      <ol className="funil-etapas">
        <li>Bronze: <strong>{f.bronze}</strong> linhas ({f.bronze_unicos} únicas)</li>
        <li>Prata: <strong>{f.prata}</strong> vínculos ({f.prata_cidadaos} cidadãos)</li>
        <li>Ouro: <strong>{f.ouro}</strong> cidadãos</li>
      </ol>
      {f.descartes.length > 0 ? (
        <table><caption>Descartes e pendências por motivo</caption>
          <thead><tr><th>Etapa</th><th>Motivo</th><th>Quantidade</th></tr></thead>
          <tbody>{f.descartes.map((d) => <tr key={d.etapa + d.motivo}><td>{d.etapa}</td><td>{d.motivo}</td><td>{d.quantidade}</td></tr>)}</tbody>
        </table>
      ) : <p>Sem descartes.</p>}
      {!f.quarentena_disponivel && <p className="nota">Quarentena de chave nula (CC9) ainda não disponível nesta versão.</p>}
    </div>
  )
}

function CamadaPainel({ conector, camada, podeRevelar }: { conector: string; camada: Camada; podeRevelar: boolean }) {
  const [t, setT] = useState<Tabelas | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [tabela, setTabela] = useState('')
  useEffect(() => {
    explorar<Tabelas>('tabelas', { conector, camada }).then((r) => { setT(r); setTabela(r.tabelas.find((x) => x.linhas > 0)?.tabela ?? '') })
      .catch(() => setErro('Não foi possível listar as tabelas.'))
  }, [conector, camada])
  if (erro) return <p role="alert">{erro}</p>
  if (!t) return <Esqueleto texto="Carregando tabelas…" />
  return (
    <div role="tabpanel">
      {t.estado.processando && <p role="status" className="aviso">{t.estado.mensagem}</p>}
      {t.vazia ? <p className="vazio">{t.mensagem_vazia}</p> : (
        <>
          <table><caption>Tabelas da camada</caption>
            <thead><tr><th>Tabela</th><th>Linhas</th><th>Última carga</th></tr></thead>
            <tbody>{t.tabelas.map((x) => (
              <tr key={x.tabela} aria-selected={x.tabela === tabela}>
                <td><button className="link" onClick={() => setTabela(x.tabela)}>{x.tabela}</button></td>
                <td>{x.linhas}</td><td>{dataHoraBR(x.ultima_carga)}</td>
              </tr>))}
            </tbody>
          </table>
          {tabela && <TabelaDetalhe key={tabela} conector={conector} camada={camada} tabela={tabela} podeRevelar={podeRevelar} />}
        </>
      )}
    </div>
  )
}

function TabelaDetalhe({ conector, camada, tabela, podeRevelar }: { conector: string; camada: Camada; tabela: string; podeRevelar: boolean }) {
  const [perfil, setPerfil] = useState<Perfil | null>(null)
  const [am, setAm] = useState<Amostra | null>(null)
  const [pagina, setPagina] = useState(1)
  const [rascunho, setRascunho] = useState<Record<string, string>>({})
  const [filtros, setFiltros] = useState<Record<string, string>>({})
  const [pend, setPend] = useState(false)
  const [revelados, setRevelados] = useState<Record<string, unknown>>({})
  const [revelar, setRevelar] = useState<{ id: unknown; coluna: string } | null>(null)
  const [linhagem, setLinhagem] = useState<string | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const base = { conector, camada, tabela }

  useEffect(() => { explorar<Perfil>('perfil', { conector, camada, tabela }).then(setPerfil).catch(() => setPerfil(null)) }, [conector, camada, tabela])
  useEffect(() => {
    setErro(null)
    explorar<Amostra>('amostra', { conector, camada, tabela, pagina, filtros, somente_pendencias: pend }).then(setAm)
      .catch(() => setErro('Não foi possível carregar a amostra.'))
  }, [conector, camada, tabela, pagina, filtros, pend])

  const aplicar = (e: React.FormEvent) => { e.preventDefault(); setPagina(1); setFiltros(rascunho) }
  const chave = (id: unknown, c: string) => `${String(id)}:${c}`
  const sensiveis = new Set(perfil?.colunas.filter((c) => c.sensivel).map((c) => c.coluna) ?? [])

  return (
    <div className="detalhe">
      <h2>{tabela}</h2>
      {perfil && (
        <details open><summary>Perfil das colunas ({perfil.linhas} linhas)</summary>
          <table><caption className="sr-only">Perfil por coluna</caption>
            <thead><tr><th>Coluna</th><th>Tipo</th><th>% preenchido</th><th>% nulo</th><th>Distintos</th><th>Exemplos</th></tr></thead>
            <tbody>{perfil.colunas.map((c) => (
              <tr key={c.coluna}><td>{c.coluna}{c.sensivel && <span className="selo" title="Dado pessoal, mascarado"> 🔒</span>}</td>
                <td>{c.tipo}</td><td>{c.preenchido_pct}%</td><td>{c.nulo_pct}%</td><td>{c.distintos}</td>
                <td>{c.exemplos.map(texto).join(' · ') || '—'}</td></tr>))}
            </tbody>
          </table>
        </details>
      )}
      {camada === 'prata' && (
        <label className="campo"><input type="checkbox" checked={pend} onChange={(e) => { setPagina(1); setPend(e.target.checked) }} /> Só pendências (sem correspondência / em quarentena)</label>
      )}
      {am && (
        <form onSubmit={aplicar} className="filtros" aria-label="Filtros">
          {(pend ? ['motivo'] : am.colunas.filter((c) => !['id', 'extraido_em', 'quarentena'].includes(c)).slice(0, 6)).map((c) => (
            <label key={c} className="campo">{c}
              <input value={rascunho[c] ?? ''} onChange={(e) => setRascunho({ ...rascunho, [c]: e.target.value })}
                placeholder={sensiveis.has(c) ? 'valor exato' : 'contém'} autoComplete="off" />
            </label>
          ))}
          <button className="btn" type="submit">Filtrar</button>
        </form>
      )}
      {erro && <p role="alert">{erro}</p>}
      {am && (
        <>
          <div className="tabela-rolagem">
            <table><caption>Amostra: {am.total} linhas, página {am.pagina}</caption>
              <thead><tr>{am.colunas.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>{am.linhas.map((l, i) => {
                const id = l[am.id_coluna]
                return (
                  <tr key={String(id ?? i)}>{am.colunas.map((c) => {
                    const k = chave(id, c)
                    const v = k in revelados ? revelados[k] : l[c]
                    return (
                      <td key={c}>
                        {am.cidadao_coluna === c && camada === 'ouro' && l[c]
                          ? <button className="link" onClick={() => setLinhagem(String(l[c]))}>{texto(v)}</button> : texto(v)}
                        {podeRevelar && !pend && sensiveis.has(c) && l[c] != null && !(k in revelados) && (
                          <button className="link revelar" aria-label={`Revelar ${c}`} onClick={() => setRevelar({ id, coluna: c })}>revelar</button>)}
                      </td>)
                  })}</tr>)
              })}</tbody>
            </table>
          </div>
          <nav className="paginacao" aria-label="Paginação">
            <button className="btn secundario" disabled={!am.tem_anterior} onClick={() => setPagina(pagina - 1)}>Anterior</button>
            <span>Página {am.pagina}</span>
            <button className="btn secundario" disabled={!am.tem_proxima} onClick={() => setPagina(pagina + 1)}>Próxima</button>
          </nav>
        </>
      )}
      {revelar && <DialogoRevelar {...base} alvo={revelar} fechar={() => setRevelar(null)}
        revelado={(v, s) => {
          const k = chave(revelar.id, revelar.coluna)
          setRevelados((r) => ({ ...r, [k]: v }))
          window.setTimeout(() => setRevelados((r) => { const n = { ...r }; delete n[k]; return n }), s * 1000)
          setRevelar(null)
        }} />}
      {linhagem && <LinhagemPainel cidadao={linhagem} fechar={() => setLinhagem(null)} />}
    </div>
  )
}

function DialogoRevelar({ conector, camada, tabela, alvo, fechar, revelado }: {
  conector: string; camada: Camada; tabela: string; alvo: { id: unknown; coluna: string }; fechar: () => void; revelado: (v: unknown, s: number) => void
}) {
  const [motivo, setMotivo] = useState('')
  const [erro, setErro] = useState<string | null>(null)
  const enviar = async () => {
    try {
      const r = await explorar<{ valor: unknown; remascarar_em_segundos: number }>('revelar', { conector, camada, tabela, linha_id: alvo.id, coluna: alvo.coluna, motivo })
      revelado(r.valor, r.remascarar_em_segundos)
    } catch (e) {
      setErro(e instanceof ApiErro && e.status === 400 ? `Informe o motivo (mínimo ${MOTIVO_MIN} caracteres).` : 'Não foi possível revelar.')
    }
  }
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="t-rev" className="dialogo">
      <h2 id="t-rev">Revelar {alvo.coluna}</h2>
      <label className="campo">Motivo<textarea value={motivo} onChange={(e) => setMotivo(e.target.value)} /></label>
      {erro && <p role="alert">{erro}</p>}
      <p className="nota">O valor volta a ficar mascarado depois de 60 segundos. A revelação fica registrada na auditoria.</p>
      <button className="btn" onClick={enviar} disabled={motivo.trim().length < MOTIVO_MIN}>Revelar</button>
      <button className="btn secundario" onClick={fechar}>Cancelar</button>
    </div>
  )
}

function LinhagemPainel({ cidadao, fechar }: { cidadao: string; fechar: () => void }) {
  const [l, setL] = useState<Linhagem | null>(null)
  useEffect(() => { explorar<Linhagem>('linhagem', { cidadao_id: cidadao }).then(setL).catch(() => setL(null)) }, [cidadao])
  return (
    <div className="cartao linhagem" aria-label="Linhagem do cidadão">
      <h3>De onde veio este cidadão</h3>
      {!l ? <Esqueleto texto="Carregando linhagem…" /> : (
        <table><thead><tr><th>Origem</th><th>Tabela</th><th>Chave</th><th>Bronze</th><th>Método</th><th>Confiança</th></tr></thead>
          <tbody>{l.prata.map((p) => (
            <tr key={p.bronze_id}><td>{p.origem}</td><td>{p.tabela}</td><td>{p.chave}</td><td>#{p.bronze_id}</td><td>{p.metodo}</td><td>{p.confianca}</td></tr>))}
          </tbody></table>
      )}
      <button className="btn secundario" onClick={fechar}>Fechar</button>
    </div>
  )
}

import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, ApiErro, dataHoraBR } from '../api'
import { useAuth } from '../auth'
import AgendaCargas from '../AgendaCargas'
import PainelProgresso from '../PainelProgresso'
import { Esqueleto, SeloStatus } from '../componentes'
import PainelBases, { CARGA_EM_PREPARACAO, type Base } from './PainelBases'

type Exec = { id: number; tipo: string; status: string; iniciado_em: string; finalizado_em: string | null; lidos: number; novos: number; atualizados: number; ignorados: number; lotes: number }
type Conector = { id: number; codigo: string; nome: string; status: string; ultima_execucao: Exec | null; credencial: string; amostra_limite: number; tabelas: { nome: string; chave: string; campo_incremental: string | null }[] }
type Resp = { conectores: Conector[]; execucoes: Exec[]; bases?: Base[]; carga_habilitada?: boolean }

export default function Conectores() {
  const { sessao } = useAuth()
  const [params] = useSearchParams()
  const [d, setD] = useState<Resp | null>(null)
  const [erro, setErro] = useState(false)
  const [aviso, setAviso] = useState('')
  const [executando, setExecutando] = useState(false)
  const [form, setForm] = useState(params.get('novo') === '1')
  const podeExecutar = sessao?.perfil === 'admin' || sessao?.perfil === 'operador'
  const ehAdmin = sessao?.perfil === 'admin'

  const carregar = useCallback(() => {
    setErro(false); setD(null)
    api<Resp>('/conectores').then(setD).catch(() => setErro(true))
  }, [])
  useEffect(carregar, [carregar])

  const testar = async (c: Conector) => {
    setAviso('')
    try {
      const r = await api<{ ok: boolean; erro: string | null }>(`/conectores/${encodeURIComponent(c.codigo)}/testar`, { method: 'POST' })
      setAviso(r.ok ? `Conexão com ${c.codigo} bem-sucedida.` : (r.erro ?? `Falha ao conectar em ${c.codigo}.`))
    } catch (e) {
      setAviso(e instanceof ApiErro && e.status === 403 ? 'Seu perfil não pode testar conexões.' : `Falha ao conectar em ${c.codigo}. Verifique a configuração do ambiente.`)
    }
  }
  const executar = async () => {
    setExecutando(true); setAviso('')
    try {
      const r = await api<Exec>('/conectores/demo/executar', { method: 'POST', body: '{}' })
      setAviso(r.status === 'sem_novidades' ? 'Sem novidades · 0 novos, 0 atualizados' : `Carga concluída: ${r.status}.`)
      carregar()
    } catch {
      setAviso('Não foi possível executar a carga.')
    } finally {
      setExecutando(false)
    }
  }

  return (
    <section aria-labelledby="t-con">
      <header className="page-head">
        <p className="kicker">Dados</p>
        <h1 id="t-con">Conectores</h1>
        <p className="sutil">Bases municipais, camadas de dados e histórico de cargas.</p>
      </header>
      <PainelProgresso />
      <AgendaCargas ehAdmin={ehAdmin} />
      <PainelBases bases={erro ? null : d ? (d.bases ?? []) : null} erro={erro} onRecarregar={carregar} ehAdmin={ehAdmin} cargaHabilitada={d?.carga_habilitada === true} />
      <h2>Sistemas cadastrados (demonstração)</h2>
      <div className="acoes">
        {podeExecutar && <button className="btn" onClick={executar} disabled={executando || d?.carga_habilitada !== true}>Executar carga (demo)</button>}
        {podeExecutar && d && d.carga_habilitada !== true && <span className="sutil">{CARGA_EM_PREPARACAO}</span>}
        {ehAdmin && <button className="btn secundario" onClick={() => setForm(!form)}>Cadastrar conector</button>}
      </div>
      {executando && <div role="progressbar" aria-label="Executando carga" aria-busy="true" className="progresso">Executando carga…</div>}
      <div aria-live="polite">{aviso && <p className="aviso">{aviso}</p>}</div>
      {form && ehAdmin && <FormConector aoSalvar={() => { setForm(false); carregar() }} />}
      {erro ? <p className="sutil">Sem dados.</p>
        : !d ? <Esqueleto />
        : d.conectores.length === 0 ? (
          <div className="vazio">
            <p>Nenhum conector cadastrado. Cadastre a primeira fonte, por exemplo a base da Saúde.</p>
            {ehAdmin && !form && <button className="btn" onClick={() => setForm(true)}>Cadastrar conector</button>}
          </div>
        ) : (
          <>
            <ul className="lista-cartoes" aria-label="Conectores">
              {d.conectores.map((c) => (
                <li key={c.id} className="cartao">
                  <h2>{c.nome} ({c.codigo})</h2>
                  <p>Status: <span className="selo mapeado"><i aria-hidden="true" />{c.status}</span></p>
                  <p>Última execução: {c.ultima_execucao ? `${dataHoraBR(c.ultima_execucao.iniciado_em)} · ${c.ultima_execucao.lidos} lidos e ${c.ultima_execucao.novos} novos` : '—'}</p>
                  <p>Credencial: {c.credencial} · Amostra: {c.amostra_limite} linhas</p>
                  <button className="btn secundario" onClick={() => testar(c)}>Testar conexão</button>
                </li>
              ))}
            </ul>
            <h2>Histórico de execuções</h2>
            <table className="tabela">
              <caption className="sr-only">Histórico de execuções</caption>
              <thead><tr><th scope="col">Status</th><th scope="col">Início</th><th scope="col">Fim</th><th scope="col">Lidos</th><th scope="col">Novos</th><th scope="col">Atualizados</th><th scope="col">Ignorados</th></tr></thead>
              <tbody>
                {d.execucoes.map((e) => (
                  <tr key={e.id}>
                    <td data-rotulo="Status"><SeloStatus {...e} /></td>
                    <td data-rotulo="Início">{dataHoraBR(e.iniciado_em)}</td>
                    <td data-rotulo="Fim">{dataHoraBR(e.finalizado_em)}</td>
                    <td data-rotulo="Lidos">{e.lidos}</td><td data-rotulo="Novos">{e.novos}</td>
                    <td data-rotulo="Atualizados">{e.atualizados}</td><td data-rotulo="Ignorados">{e.ignorados}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
    </section>
  )
}

function FormConector({ aoSalvar }: { aoSalvar: () => void }) {
  const [v, setV] = useState({ codigo: '', nome: '', tabela: '', chave: '', incremental: '' })
  const [erro, setErro] = useState('')
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => setV({ ...v, [k]: e.target.value })
  const salvar = async (e: FormEvent) => {
    e.preventDefault(); setErro('')
    try {
      const s = await api<{ id: number }>('/catalogo/sistemas', { method: 'POST', body: JSON.stringify({ codigo: v.codigo, nome: v.nome }) })
      if (v.tabela) await api(`/catalogo/sistemas/${s.id}/tabelas`, { method: 'POST', body: JSON.stringify({ nome: v.tabela, chave: v.chave, campo_incremental: v.incremental || null }) })
      aoSalvar()
    } catch (er) {
      setErro(er instanceof ApiErro ? er.message : 'Não foi possível salvar.')
    }
  }
  return (
    <form className="cartao form" onSubmit={salvar} aria-label="Cadastrar conector">
      <label htmlFor="f-nome">Nome</label><input id="f-nome" value={v.nome} onChange={set('nome')} required />
      <label htmlFor="f-codigo">Sistema (código)</label><input id="f-codigo" value={v.codigo} onChange={set('codigo')} required pattern="[A-Za-z0-9_]+" />
      <label htmlFor="f-tabela">Tabela</label><input id="f-tabela" value={v.tabela} onChange={set('tabela')} />
      <label htmlFor="f-chave">Chave</label><input id="f-chave" value={v.chave} onChange={set('chave')} required={!!v.tabela} />
      <label htmlFor="f-inc">Campo incremental</label><input id="f-inc" value={v.incremental} onChange={set('incremental')} />
      <p>Credencial: Configurada via variável de ambiente · Limite de amostra: 100</p>
      <div aria-live="assertive">{erro && <p className="erro-texto">{erro}</p>}</div>
      <button className="btn" type="submit">Salvar</button>
    </form>
  )
}

import { useEffect, useRef, useState } from 'react'
import { api, ApiErro, dataBR, dataHoraBR } from './api'
import { useAuth } from './auth'

const MOTIVO_MIN = Number(import.meta.env.VITE_REVELAR_MOTIVO_MIN ?? 10)
const HOST = 'cidades.sei.sp.gov.br'

type Andamento = { data_hora: string | null; descricao: string | null }
export type ProcessoSei = {
  id_procedimento: string; protocolo: string; tipo_sei: string | null; servico_carta: string | null; rotulo: string
  data_autuacao: string | null; unidade_atual: string | null; ultimo_andamento: Andamento | null
  status: string; documentos: number; link: string | null; tem_outros: boolean
}
export type RespSei = { ficha_id: string; fonte_liberada: boolean; mensagem: string | null; total: number; processos: ProcessoSei[] }
type Outro = { nome: string | null; cpf: string | null; cnpj: string | null }

const valida = (r: unknown): r is RespSei => !!r && typeof (r as RespSei).fonte_liberada === 'boolean'
  && Array.isArray((r as RespSei).processos)

/** Só a Pesquisa Pública de Franca, e sem parâmetro de documento ou nome. */
export function linkSeguro(href: string | null | undefined): string | null {
  if (!href) return null
  try {
    const u = new URL(href)
    if (u.protocol !== 'https:' || u.host !== HOST || !u.pathname.startsWith('/franca')) return null
    const chaves = [...u.searchParams.keys()]
    if (chaves.some((k) => /cpf|cnpj|nome|interessado/i.test(k))) return null
    return u.toString()
  } catch {
    return null
  }
}

/** Bloco Processos SEI. A busca usa o id opaco da ficha; abrir a rota grava a auditoria. */
export default function ProcessosSei({ caminho }: { caminho: string }) {
  const { sessao } = useAuth()
  const [resp, setResp] = useState<RespSei | null | false | 'sem_acesso'>(null)
  useEffect(() => {
    let vivo = true
    setResp(null)
    api<unknown>(caminho).then((r) => { if (vivo) setResp(valida(r) ? r : false) }).catch((e) => {
      if (vivo) setResp(e instanceof ApiErro && e.status === 403 ? 'sem_acesso' : false)
    })
    return () => { vivo = false }
  }, [caminho])

  return (
    <section className="cartao portal-bloco" id="area-sei" aria-labelledby="t-sei" data-testid="processos-sei">
      <h3 id="t-sei">Processos SEI</h3>
      {resp === null ? <p className="sutil">Carregando processos SEI…</p>
        : resp === 'sem_acesso' ? <p className="area-indefinida">Seu perfil não tem acesso a esta área. Isso não quer dizer que não haja processo.</p>
        : resp === false ? <p className="area-indefinida">Não foi possível ler os processos SEI agora. Isso não quer dizer que não haja processo.</p>
        : !resp.fonte_liberada ? <p className="area-vazia" data-testid="sei-mensagem">Fonte SEI ainda não liberada</p>
        : resp.processos.length === 0 ? <p className="area-vazia" data-testid="sei-mensagem">Nenhum processo SEI</p>
        : (
          <ul className="sei-lista" aria-label="Processos SEI">
            {resp.processos.map((p) => <Item key={p.id_procedimento || p.protocolo} p={p} caminho={caminho} perfil={sessao?.perfil} />)}
          </ul>
        )}
    </section>
  )
}

function Item({ p, caminho, perfil }: { p: ProcessoSei; caminho: string; perfil?: string }) {
  const link = linkSeguro(p.link)
  const and = p.ultimo_andamento
  return (
    <li className="sei-item">
      <h4>{p.rotulo || p.tipo_sei || 'Tipo não informado'}</h4>
      {p.servico_carta && p.tipo_sei && p.servico_carta !== p.tipo_sei && <p className="sei-meta">Tipo no SEI: {p.tipo_sei}</p>}
      <p className="sei-meta">Protocolo: {p.protocolo || '—'}</p>
      <p className="sei-meta">Autuação: {dataBR(p.data_autuacao)} · Unidade atual: {p.unidade_atual || 'Não informada'}</p>
      <p className="sei-meta">Status: <span className="selo neutro">{p.status}</span> · {p.documentos} {p.documentos === 1 ? 'documento' : 'documentos'}</p>
      <p className="sei-meta">Último andamento: {and?.data_hora ? dataHoraBR(and.data_hora) : 'sem data'}{and?.descricao ? ` — ${and.descricao}` : ''}</p>
      {link && <a href={link} target="_blank" rel="noopener noreferrer">Abrir no SEI</a>}
      {p.tem_outros && <Outros caminho={caminho} id={p.id_procedimento} perfil={perfil} />}
    </li>
  )
}

function Outros({ caminho, id, perfil }: { caminho: string; id: string; perfil?: string }) {
  const [lista, setLista] = useState<Outro[] | null>(null)
  const [dialogo, setDialogo] = useState(false)
  const [aviso, setAviso] = useState('')
  const btn = useRef<HTMLButtonElement>(null)
  const ver = async () => {
    setAviso('')
    try {
      const r = await api<{ interessados: Outro[] }>(`${caminho}/interessados`, { method: 'POST', body: JSON.stringify({ id_procedimento: id }) })
      setLista(r.interessados)
    } catch {
      setAviso('Não foi possível listar os outros interessados.')
    }
  }
  const revelado = (itens: Outro[], segundos: number) => {
    setLista(itens)
    setAviso('Revelação registrada na auditoria.')
    window.setTimeout(() => { setLista(null); setAviso('') }, segundos * 1000)
  }
  return (
    <div>
      {lista === null && <button ref={btn} className="btn-link" type="button" onClick={ver}>Ver outros interessados</button>}
      {lista && (
        <ul aria-label="Outros interessados">
          {lista.map((i, n) => (
            <li key={n}>{i.nome || 'Nome não informado'}{i.cpf ? ` · CPF ${i.cpf}` : ''}{i.cnpj ? ` · CNPJ ${i.cnpj}` : ''}</li>
          ))}
        </ul>
      )}
      {lista && perfil === 'admin_lgpd' && (
        <button className="btn-link" type="button" onClick={() => setDialogo(true)}>Revelar outros interessados</button>
      )}
      <div aria-live="polite">{aviso && <p className="aviso">{aviso}</p>}</div>
      {dialogo && <Dialogo caminho={caminho} id={id} fechar={() => { setDialogo(false); btn.current?.focus() }} aoRevelar={revelado} />}
    </div>
  )
}

function Dialogo({ caminho, id, fechar, aoRevelar }: { caminho: string; id: string; fechar: () => void; aoRevelar: (itens: Outro[], s: number) => void }) {
  const [motivo, setMotivo] = useState('')
  const [erro, setErro] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('textarea')?.focus()
    const f = (e: KeyboardEvent) => { if (e.key === 'Escape') fechar() }
    document.addEventListener('keydown', f)
    return () => document.removeEventListener('keydown', f)
  }, [fechar])
  const revelar = async () => {
    setErro('')
    try {
      const r = await api<{ interessados: Outro[]; remascarar_em_segundos: number }>(`${caminho}/interessados/revelar`, {
        method: 'POST', body: JSON.stringify({ id_procedimento: id, motivo }) })
      aoRevelar(r.interessados, r.remascarar_em_segundos ?? 60)
      fechar()
    } catch (e) {
      setErro(e instanceof ApiErro && e.status === 400 ? 'Informe o motivo para revelar os interessados.' : 'Não foi possível revelar.')
    }
  }
  return (
    <div className="veu-dialogo">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="t-revelar-sei" className="dialogo">
        <h2 id="t-revelar-sei">Revelar interessados</h2>
        <p>Esta ação fica registrada na auditoria com seu usuário, data e motivo.</p>
        <label htmlFor="motivo-sei">Motivo</label>
        <textarea id="motivo-sei" value={motivo} onChange={(e) => setMotivo(e.target.value)} required />
        <small>Mínimo de {MOTIVO_MIN} caracteres.</small>
        <div aria-live="assertive">{erro && <p className="erro-texto">{erro}</p>}</div>
        <div className="acoes">
          <button className="btn secundario" type="button" onClick={fechar}>Cancelar</button>
          <button className="btn" type="button" onClick={revelar} disabled={motivo.trim().length < MOTIVO_MIN}>Revelar</button>
        </div>
      </div>
    </div>
  )
}

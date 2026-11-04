import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiErro, dataHoraBR } from './api'

type Ex = { status: string; mensagem: string | null; passo: string | null; lidos: number | null; finalizado_em: string | null }
type Estado = { meses: number; de: number | null; ate: number | null; em_andamento: Ex | null; ultima_execucao: Ex | null }
const estadoOk = (r: unknown): r is Estado => !!r && typeof (r as Estado).meses === 'number'
const comp = (c: number | null) => (c ? `${String(c % 100).padStart(2, '0')}/${Math.floor(c / 100)}` : '')

/** ANS e IBGE na tela Conectores: meses carregados e "Carregar agora" (só admin). */
export default function CargaAns({ ehAdmin }: { ehAdmin: boolean }) {
  const [d, setD] = useState<Estado | null>(null)
  const [erro, setErro] = useState(false)
  const [aviso, setAviso] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const carregar = useCallback(() => {
    api<unknown>('/cobertura/estado').then((r) => {
      if (!estadoOk(r)) { setErro(true); return }
      setD(r); setErro(false)
      if (r.em_andamento) timer.current = setTimeout(carregar, 10000)
    }).catch(() => setErro(true))
  }, [])
  useEffect(() => { carregar(); return () => { if (timer.current) clearTimeout(timer.current) } }, [carregar])
  const iniciar = async () => {
    setAviso(null)
    try { await api('/cobertura/carregar', { method: 'POST' }); carregar() } catch (e) { setAviso(e instanceof ApiErro ? e.message : 'Não foi possível iniciar a carga.') }
  }
  const ult = d?.ultima_execucao
  return (
    <section className="cartao audesp" id="fonte-ans" aria-labelledby="t-ans">
      <div className="ph-audesp">
        <div>
          <h2 id="t-ans">ANS e IBGE (cobertura de planos)</h2>
          <p className="sutil">Dados abertos de beneficiários por município, sem pessoa. Base do painel <Link to="/painel/cobertura">Cobertura de planos</Link>.</p>
        </div>
        {ult && !d?.em_andamento && <span className={`selo ${ult.status === 'sucesso' ? 'sucesso' : 'falha'}`}><i aria-hidden="true" />{ult.status === 'sucesso' ? 'Concluída' : ult.status === 'parcial' ? 'Parcial' : 'Falhou'} · {dataHoraBR(ult.finalizado_em)}</span>}
      </div>
      {erro ? <p className="sutil">Não foi possível carregar o estado da ANS.{' '}<button className="link-btn" type="button" onClick={carregar}>Tentar de novo</button></p>
        : !d ? <p className="sutil">Carregando…</p>
        : <p>{d.meses ? `${d.meses} meses carregados, de ${comp(d.de)} a ${comp(d.ate)}.` : 'Nenhum mês carregado ainda.'}{d.em_andamento ? ` Carregando ${d.em_andamento.passo ?? ''}…` : ''}{ult && ult.status !== 'sucesso' && !d.em_andamento && ult.mensagem ? <span className="sutil"> {ult.mensagem}</span> : null}</p>}
      {ehAdmin && (
        <div className="acoes">
          <button className="btn secundario" type="button" onClick={iniciar} disabled={!!d?.em_andamento}>{d?.em_andamento ? 'Carregando…' : 'Carregar agora'}</button>
          <span className="sutil">O histórico completo leva cerca de meia hora. Depois, recalcule os cubos.</span>
        </div>
      )}
      <div aria-live="polite">{aviso && <p className="aviso erro">{aviso}</p>}</div>
    </section>
  )
}

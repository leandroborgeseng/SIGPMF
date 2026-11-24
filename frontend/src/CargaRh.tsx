import { useCallback, useEffect, useState } from 'react'
import { api, ApiErro, dataHoraBR } from './api'
import { classeStatus, STATUS_EXEC } from './AgendaCargas'

type Exec = { id: number; status: string; mensagem: string | null; mes_atual: string | null; lidos: number; iniciado_em: string; finalizado_em: string | null }
type Estado = { meses: number; linhas: number; primeiro: [number, number] | null; ultimo: [number, number] | null; em_andamento: Exec | null; ultima_execucao: Exec | null }
const estadoOk = (r: unknown): r is Estado => !!r && typeof (r as Estado).meses === 'number'
const TEXTO: Record<string, string> = { ...STATUS_EXEC, interrompida: 'Interrompida' }
const mesAno = (p: [number, number]) => `${String(p[1]).padStart(2, '0')}/${p[0]}`

/** RH1: remuneração dos servidores (Portal da Transparência) na tela Conectores: o que está carregado e "Carregar agora". */
export default function CargaRh({ ehAdmin }: { ehAdmin: boolean }) {
  const [d, setD] = useState<Estado | null>(null)
  const [erro, setErro] = useState(false)
  const [aviso, setAviso] = useState<{ texto: string; erro?: boolean } | null>(null)
  const carregar = useCallback(() => {
    api<unknown>('/rh/estado').then((r) => { if (estadoOk(r)) { setD(r); setErro(false) } else setErro(true) }).catch(() => setErro(true))
  }, [])
  useEffect(carregar, [carregar])
  const rodando = d?.em_andamento ?? null
  useEffect(() => {
    if (!rodando) return
    const t = window.setTimeout(carregar, 3000)
    return () => window.clearTimeout(t)
  }, [rodando, d, carregar])
  const carregarAgora = async () => {
    setAviso(null)
    try {
      const r = await api<unknown>('/rh/carregar', { method: 'POST', body: '{}' })
      if (estadoOk(r)) setD(r); else carregar()
      setAviso({ texto: 'Carga iniciada: na primeira vez, baixa o histórico desde 2012 e leva alguns minutos.' })
    } catch (e) {
      setAviso({ texto: e instanceof ApiErro ? e.message : 'Não foi possível iniciar a carga.', erro: true })
    }
  }
  const ult = d?.ultima_execucao
  return (
    <section className="cartao audesp" id="fonte-rh" aria-labelledby="t-rh">
      <div className="ph-audesp">
        <div>
          <h2 id="t-rh">RH (Portal da Transparência)</h2>
          <p className="sutil">Remuneração dos servidores publicada pela prefeitura, mês a mês. Nome e matrícula ficam guardados para o cruzamento com o Senior; o painel mostra só números somados.</p>
        </div>
        {rodando
          ? <span className="selo em_andamento"><i aria-hidden="true" />Carregando{rodando.mes_atual ? ` · ${rodando.mes_atual}` : ''}</span>
          : ult && <span className={`selo ${ult.status === 'parcial' ? 'atencao' : classeStatus(ult.status === 'sem_novidades' ? 'sucesso' : ult.status)}`}>
              <i aria-hidden="true" />{TEXTO[ult.status] ?? ult.status} · {dataHoraBR(ult.finalizado_em ?? ult.iniciado_em)}</span>}
      </div>
      {erro ? <p className="sutil">Não foi possível carregar o estado do RH.{' '}<button className="link-btn" type="button" onClick={carregar}>Tentar de novo</button></p>
        : !d ? <p className="sutil">Carregando…</p>
        : <p>{d.primeiro && d.ultimo ? `Carregado de ${mesAno(d.primeiro)} a ${mesAno(d.ultimo)}: ${d.meses} folhas e ${new Intl.NumberFormat('pt-BR').format(d.linhas)} pagamentos.` : 'Nenhum mês carregado ainda.'}</p>}
      {!rodando && ult?.mensagem && <p className="sutil">{ult.mensagem}</p>}
      {ehAdmin && d && <div className="acoes"><button className="btn" type="button" onClick={carregarAgora} disabled={!!rodando}>Carregar agora</button></div>}
      <div aria-live="polite">{aviso && <p className={aviso.erro ? 'aviso erro' : 'aviso'}>{aviso.texto}</p>}</div>
    </section>
  )
}

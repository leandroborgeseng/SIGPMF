import { useEffect, useState } from 'react'
import { api, ApiErro, dataHoraBR } from './api'

/** PR-A (UXA1–UXA5, CT1–CT4): amostra obrigatória antes da carga completa, com botão, resultado e aprovação no card. */
export type ResumoAmostra = {
  id: number; status: string; finalizado_em: string | null; lidos: number; gravados: number; com_erro: number
  motivo: string | null; aprovada: boolean
}
type Exec = { id: number; status: string; em_andamento?: boolean; lidos: number; ignorados: number; mensagem: string | null; erro?: { motivo: string } | null }
type LinhaErro = { id: number; tabela: string; motivo: string; campos_nulos: string[]; referencia: string }

export const INTERVALO_MS = 2000
const milhar = (n: number) => new Intl.NumberFormat('pt-BR').format(n)
const linhas = (n: number) => `${milhar(n)} ${n === 1 ? 'linha' : 'linhas'}`
const espera = (ms: number) => new Promise((r) => setTimeout(r, ms))
export const rotuloRodar = (limite: number) => `Rodar amostra (${milhar(limite)} linhas)`

export default function PainelAmostra({ conector, nome, limite, inicial, onMudou, desabilitado = false }: {
  conector: string; nome: string; limite: number; inicial: ResumoAmostra | null; onMudou: () => void; desabilitado?: boolean
}) {
  const [am, setAm] = useState<ResumoAmostra | null>(inicial)
  const [rodando, setRodando] = useState(false)
  const [aprovando, setAprovando] = useState(false)
  const [erro, setErro] = useState('')
  const [erros, setErros] = useState<LinhaErro[] | null>(null)
  const [errosAbertos, setErrosAbertos] = useState(false)
  // a lista recarregada traz o resumo do servidor (fonte da verdade): atualiza o card quando ele muda
  const chave = inicial ? `${inicial.id}:${inicial.status}:${inicial.aprovada}` : ''
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (inicial) setAm(inicial) }, [chave])

  const rodar = async () => {
    setRodando(true); setErro(''); setErros(null); setErrosAbertos(false)
    try {
      let r = await api<Exec>(`/conectores/${conector}/amostra`, { method: 'POST' })
      while (r.em_andamento) {
        await espera(INTERVALO_MS)
        r = await api<Exec>(`/conectores/${conector}/amostra/${r.id}`)
      }
      setAm({ id: r.id, status: r.status, finalizado_em: null, lidos: r.lidos, gravados: 0, com_erro: r.ignorados ?? 0,
        motivo: r.status === 'falha' ? (r.erro?.motivo ?? r.mensagem ?? 'erro') : null, aprovada: false })
      onMudou()  // a lista volta com o resumo da amostra e o motivo novo da carga completa ("Aprovar amostra")
    } catch (e) {
      setAm(null)
      setErro(`A amostra falhou: ${e instanceof ApiErro ? e.message : 'erro inesperado'}. Corrija e rode de novo.`)
    } finally {
      setRodando(false)
    }
  }
  const aprovar = async () => {
    if (!am) return
    setAprovando(true); setErro('')
    try {
      setAm(await api<ResumoAmostra>(`/conectores/${conector}/amostra/${am.id}/aprovar`, { method: 'POST' }))
      onMudou()
    } catch (e) {
      setErro(e instanceof ApiErro ? e.message : 'Não foi possível aprovar a amostra.')
    } finally {
      setAprovando(false)
    }
  }
  const verErros = async () => {
    setErrosAbertos(!errosAbertos)
    if (erros !== null || errosAbertos) return
    try {
      setErros((await api<{ linhas: LinhaErro[] }>(`/conectores/${conector}/completa/quarentena`)).linhas)
    } catch {
      setErros([])
    }
  }

  const falhou = am?.status === 'falha'
  const ok = am !== null && !falhou
  const idRes = `amostra-res-${conector}`
  return (
    <div className="painel-amostra" aria-labelledby={`t-am-${conector}`}>
      <p id={`t-am-${conector}`} className="sr-only">Amostra de {nome}</p>
      {ok && am.aprovada && <p><span className="selo pronto"><i aria-hidden="true" />Amostra aprovada</span></p>}
      {(!ok || rodando) && (
        <button className="btn" disabled={rodando || desabilitado} aria-busy={rodando} onClick={rodar}
          aria-label={rodando ? `Rodando amostra de ${nome}…` : `${rotuloRodar(limite)} de ${nome}`}>
          {rodando ? <><span className="spinner" aria-hidden="true" /> Rodando amostra…</> : rotuloRodar(limite)}
        </button>
      )}
      <div aria-live="polite">
        {erro && <p role="alert" className="aviso erro">{erro}</p>}
        {falhou && !erro && <p role="alert" className="aviso erro">A amostra falhou: {am.motivo}. Corrija e rode de novo.</p>}
        {ok && !rodando && (
          <div id={idRes} className="resultado-amostra">
            <p>Amostra concluída: {linhas(am.lidos)} lidas, {milhar(am.com_erro)} com erro
              {am.finalizado_em ? <span className="sutil"> · {dataHoraBR(am.finalizado_em)}</span> : null}</p>
            {am.com_erro > 0 && (
              <button className="btn-link" aria-label={`Ver erros da amostra de ${nome}`} aria-expanded={errosAbertos} aria-controls={`erros-${conector}`} onClick={verErros}>Ver erros</button>
            )}
            {errosAbertos && (
              <div id={`erros-${conector}`}>
                {erros === null ? <p className="sutil">Carregando erros…</p>
                  : erros.length === 0 ? <p className="sutil">Nenhum erro para listar.</p>
                  : <ul className="lista-erros">{erros.map((l) => <li key={l.id}>{l.tabela} · <code>{l.referencia}</code>: {l.motivo}</li>)}</ul>}
              </div>
            )}
            <p className="acoes">
              {!am.aprovada && <button className="btn" disabled={aprovando} onClick={aprovar} aria-label={`Aprovar amostra de ${nome}`}>Aprovar amostra</button>}
              {' '}<button className="btn outlined" disabled={desabilitado} onClick={rodar} aria-label={`Rodar de novo a amostra de ${nome}`}>Rodar de novo</button>
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, dataHoraBR } from '../api'
import { useAuth } from '../auth'
import { Erro, Esqueleto, SeloStatus } from '../componentes'

type Exec = { tipo: string; status: string; iniciado_em: string; finalizado_em: string | null; lidos: number; novos: number; atualizados: number; ignorados: number }
type Dash = {
  sistemas: number; bronze: { registros: number }; silver: { cidadaos: number; pendentes: number }
  ultima_execucao: Exec | null
}

export default function Dashboard() {
  const { sessao } = useAuth()
  const [d, setD] = useState<Dash | null>(null)
  const [erro, setErro] = useState(false)
  const carregar = useCallback(() => {
    setErro(false); setD(null)
    api<Dash>('/dashboard').then(setD).catch(() => setErro(true))
  }, [])
  useEffect(carregar, [carregar])

  return (
    <section aria-labelledby="t-dash">
      <h1 id="t-dash">Dashboard</h1>
      {erro ? <Erro texto="Não foi possível carregar os indicadores. Tente novamente em instantes." onRetry={carregar} />
        : !d ? <Esqueleto linhas={4} />
        : d.sistemas === 0 ? (
          <div className="vazio">
            <p>Nenhuma fonte de dados cadastrada ainda. Cadastre o primeiro conector para começar a importar dados.</p>
            {sessao?.perfil === 'admin'
              ? <Link className="btn" to="/conectores?novo=1">Cadastrar primeiro conector</Link>
              : <p>Peça a um administrador para cadastrar o primeiro conector.</p>}
          </div>
        ) : (
          <>
            <div className="cartoes">
              <Cartao rotulo="Conectores ativos" valor={d.sistemas} />
              <Cartao rotulo="Registros importados" valor={d.bronze.registros} />
              <Cartao rotulo="Cidadãos identificados" valor={d.silver.cidadaos} />
              <Cartao rotulo="Pendências de revisão" valor={d.silver.pendentes} />
            </div>
            <h2>Última execução</h2>
            {d.ultima_execucao ? (
              <p className="ultima">
                {d.ultima_execucao.tipo} · <SeloStatus {...d.ultima_execucao} /> · {dataHoraBR(d.ultima_execucao.iniciado_em)} ·{' '}
                {d.ultima_execucao.lidos} lidos, {d.ultima_execucao.novos} novos, {d.ultima_execucao.atualizados} atualizados
              </p>
            ) : <p>Nenhuma execução ainda.</p>}
          </>
        )}
    </section>
  )
}

function Cartao({ rotulo, valor }: { rotulo: string; valor: number }) {
  return (
    <div className="cartao">
      <div className="cartao-rotulo">{rotulo}</div>
      <div className="cartao-valor">{valor.toLocaleString('pt-BR')}</div>
    </div>
  )
}

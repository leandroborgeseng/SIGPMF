import type { ReactNode } from 'react'

type Campo = { valor: string | null; fonte: string | null; atualizado_em: string | null }
type Endereco = Campo & { mais_recente?: boolean }
export type CadastroFicha = { nome_mae: Campo; nome_pai: Campo; sexo: Campo; cns: string | null; enderecos: Endereco[] }
type Imovel = { codi: string; inscricao: string | null; complemento: string | null; endereco?: string | null; desde?: string | null; ate?: string | null; ativo: boolean }
type Lancamento = { exercicio: number; tributo: string | null; parcela: number | null; vencimento: string | null; valor: number | null; situacao: string }
type Divida = { ano: number | null; saldo: number | null; em_aberto: boolean; origem: string | null }
type Empresa = { ccm: number; nome: string | null; documento: string; possui_debito: boolean | null }
export type TributarioFicha = { totais: { imoveis: number; em_aberto: number; divida_ativa: number; inscricoes_divida: number; lancamentos: number }; imoveis: Imovel[]; lancamentos: Lancamento[]; dividas: Divida[]; empresas: Empresa[] }
type ItemSaude = { data: string | null; unidade: string | null; especialidade?: string | null; vacina?: string | null; dose?: string | null }
export type SaudeFicha = { totais?: { atendimentos: number; vacinas: number }; proximos: ItemSaude[]; atendimentos: ItemSaude[]; vacinas: ItemSaude[] }
type Matricula = { escola: string | null; unidade: string | null; turma: string | null; inativa: boolean; data_alteracao: string | null }
export type EducacaoFicha = { matriculas: { inativo: boolean; matricula_atual: Matricula | null; historico: Matricula[] }[] }
type Lugar = { cemiterio: string | null; quadra: string | null; sepultura: string | null; parentesco?: string | null; ativo?: boolean | null }
export type CemiterioFicha = { titular: Lugar[]; autorizado: Lugar[]; leilao: Lugar[]; sepultamento: Lugar[] }
export type Parente = { nome: string; parentesco: string }
export type ArvoreFicha = { estado: 'ok' | 'vazio' | 'indisponivel'; parentes: Parente[] }
export type LinhaFicha = { eventos: { data: string; area: string; titulo: string; fonte: string }[] }
export type FontesFicha = { carregadas: Record<string, boolean>; nao_integradas?: string[] }
export type Leitura = 'carregando' | 'ok' | 'falha'
export type FichaVista = {
  cadastro: CadastroFicha | null
  tributario: TributarioFicha | null
  saude: SaudeFicha | null
  educacao: EducacaoFicha | null
  cemiterio: CemiterioFicha | null
  genealogia: ArvoreFicha | null
  linha: LinhaFicha | null
  fontes: FontesFicha | null
  leituras: Record<string, Leitura>
}

const SEXO: Record<string, string> = { F: 'Feminino', M: 'Masculino' }
const vazioCampo: Campo = { valor: null, fonte: null, atualizado_em: null }
const moeda = (v: number | null | undefined) => v == null ? 'Valor a consultar' : new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v)
const data = (v: string | null | undefined) => v ? new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo' }).format(new Date(v.length === 10 ? v + 'T12:00:00Z' : v)) : 'Não informado'
const onde = (l: Lugar) => [l.cemiterio, l.quadra ? `quadra ${l.quadra}` : null, l.sepultura ? `sepultura ${l.sepultura}` : null].filter(Boolean).join(' · ') || 'Local não informado'

export const fichaDemonstracao: FichaVista = {
  cadastro: {
    nome_mae: { valor: 'Mãe de Demonstração', fonte: 'Cadastro municipal · exemplo', atualizado_em: '2026-11-20' },
    nome_pai: { valor: 'Pai de Demonstração', fonte: 'Cadastro municipal · exemplo', atualizado_em: '2026-11-20' },
    sexo: { valor: 'F', fonte: 'Cadastro municipal · exemplo', atualizado_em: '2026-11-20' },
    cns: '*** 0000 0000 ****',
    enderecos: [{ valor: 'Rua de Exemplo, 100 · Franca/SP', fonte: 'Cadastro municipal · exemplo', atualizado_em: '2026-11-20', mais_recente: true }],
  },
  tributario: {
    totais: { imoveis: 1, em_aberto: 364.8, divida_ativa: 95, inscricoes_divida: 1, lancamentos: 2 },
    imoveis: [{ codi: 'demo-imovel', inscricao: 'EXEMPLO-001', complemento: 'Rua de Exemplo, 100', endereco: 'Rua de Exemplo, 100 · Franca/SP', ativo: true, desde: '2020-01-01', ate: null }],
    lancamentos: [{ exercicio: 2026, tributo: 'IPTU', parcela: 10, vencimento: '2026-10-15', valor: 182.4, situacao: 'vencido' }],
    dividas: [{ ano: 2024, saldo: 95, em_aberto: true, origem: 'IPTU — imóvel EXEMPLO-001' }],
    empresas: [],
  },
  saude: { totais: { atendimentos: 0, vacinas: 0 }, proximos: [], atendimentos: [], vacinas: [] },
  educacao: { matriculas: [] },
  cemiterio: { titular: [], autorizado: [], leilao: [], sepultamento: [] },
  genealogia: { estado: 'indisponivel', parentes: [] },
  linha: { eventos: [] },
  fontes: { carregadas: { vacinas: true, consultas: true, agendamentos: true, imoveis: true, lancamentos: true, dividas: true, empresas: true, educacao: true, cemiterio: true } },
  leituras: { cadastro: 'ok', tributario: 'ok', saude: 'ok', educacao: 'ok', cemiterio: 'ok', genealogia: 'ok', linha: 'ok', fontes: 'ok' },
}

function Area({ id, titulo, children }: { id: string; titulo: string; children: ReactNode }) {
  return <section className="pc-bloco" id={id} data-testid={id} aria-labelledby={`${id}-titulo`}><h2 id={`${id}-titulo`}>{titulo}</h2>{children}</section>
}

function miolo(leitura: Leitura | undefined, n: number, carregada: boolean | undefined, vazio: string, children: ReactNode) {
  if (leitura === 'falha') return <p>Não foi possível ler esta área agora.</p>
  if (leitura !== 'ok') return <p>Carregando…</p>
  if (n > 0) return children
  if (carregada === false) return <p>Base não carregada</p>
  if (carregada === true) return <p className="pc-vazio">{vazio}</p>
  return <p>Carregando…</p>
}

function valorCampo(campo: string, c: Campo | undefined) {
  const v = c?.valor
  if (!v) return 'Não informado'
  if (campo === 'sexo') return SEXO[v.toUpperCase()] ?? v
  return v
}

export default function MinhaFicha({ vista }: { vista: FichaVista }) {
  const l = vista.leituras
  const fontes = l.fontes === 'ok' ? vista.fontes : null
  const carregada = (chave: string) => fontes ? fontes.carregadas[chave] === true : fontes === null && l.fontes === 'ok' ? false : undefined
  const cad = vista.cadastro
  const trib = vista.tributario
  const saude = vista.saude
  const edu = vista.educacao
  const cem = vista.cemiterio
  const arv = vista.genealogia
  const enderecos = cad?.enderecos ?? []
  const imoveis = trib?.imoveis ?? []
  const lancs = trib?.lancamentos ?? []
  const divs = trib?.dividas ?? []
  const empresas = trib?.empresas ?? []
  const mats = edu?.matriculas ?? []
  const tumulos = (cem?.titular.length ?? 0) + (cem?.autorizado.length ?? 0) + (cem?.leilao.length ?? 0)
  return (
    <div className="pc-ficha">
      <Area id="area-cadastro" titulo="Cadastro">
        {l.cadastro === 'falha' ? <p>Não foi possível ler esta área agora.</p> : l.cadastro !== 'ok' || !cad ? <p>Carregando…</p> : (
          <dl>
            <div><dt>Nome da mãe</dt><dd>{valorCampo('nome_mae', cad.nome_mae ?? vazioCampo)}</dd></div>
            <div><dt>Nome do pai</dt><dd>{valorCampo('nome_pai', cad.nome_pai ?? vazioCampo)}</dd></div>
            <div><dt>Sexo</dt><dd>{valorCampo('sexo', cad.sexo ?? vazioCampo)}</dd></div>
            <div><dt>Cartão SUS</dt><dd>{cad.cns || 'Não informado'}</dd></div>
            <div><dt>Endereço</dt><dd>{enderecos.length ? enderecos.map((e, i) => <span key={i}>{e.valor || 'Não informado'}</span>) : 'Não informado'}</dd></div>
          </dl>
        )}
      </Area>
      <Area id="area-imoveis" titulo="Imóveis">
        {miolo(l.tributario, imoveis.length, carregada('imoveis'), 'Nenhum imóvel ligado ao seu cadastro.', (
          <ul className="pc-lista" aria-label="Imóveis">{imoveis.map((i) => (
            <li key={`${i.codi}-${i.desde ?? ''}`}><strong>{i.endereco || i.complemento || 'Imóvel'}</strong><span>Inscrição {i.inscricao || i.codi} · {i.ativo ? 'Posse atual' : 'Posse anterior'}</span></li>
          ))}</ul>
        ))}
      </Area>
      <Area id="area-lancamentos" titulo="Lançamentos">
        {miolo(l.tributario, lancs.length, carregada('lancamentos'), 'Nenhum lançamento no seu cadastro.', (
          <ul className="pc-lista" aria-label="Lançamentos">{lancs.map((p, i) => (
            <li key={i}><strong>{p.tributo || 'Tributo municipal'} · {p.exercicio}</strong><span>Parcela {p.parcela ?? 'única'} · {moeda(p.valor)} · vencimento {data(p.vencimento)} · {p.situacao === 'pago' ? 'Pago' : p.situacao === 'vencido' ? 'Vencido' : 'A vencer'}</span></li>
          ))}</ul>
        ))}
      </Area>
      <Area id="area-dividas" titulo="Dívida ativa">
        {miolo(l.tributario, divs.length, carregada('dividas'), 'Nenhuma dívida ativa no seu cadastro.', (
          <ul className="pc-lista" aria-label="Dívida ativa">{divs.map((d, i) => (
            <li key={i}><strong>{d.em_aberto ? 'Em aberto' : 'Sem saldo em aberto'} · {moeda(d.saldo)}</strong><span>{d.origem || 'Inscrição municipal'}{d.ano ? ` · ${d.ano}` : ''}</span></li>
          ))}</ul>
        ))}
      </Area>
      <Area id="area-empresas" titulo="Empresas">
        {miolo(l.tributario, empresas.length, carregada('empresas'), 'Nenhuma empresa no seu CPF.', (
          <ul className="pc-lista" aria-label="Empresas">{empresas.map((e) => (
            <li key={e.ccm}><strong>{e.nome || 'Nome não informado'}</strong><span>CCM {e.ccm} · {e.documento} · {e.possui_debito ? 'Com débito' : e.possui_debito === false ? 'Sem débito' : 'Débito sem informação'}</span></li>
          ))}</ul>
        ))}
      </Area>
      <Area id="area-saude" titulo="Saúde">
        <h3>Vacinas</h3>
        {miolo(l.saude, saude?.vacinas.length ?? 0, carregada('vacinas'), 'Nenhuma vacina registrada para você.', (
          <ul className="pc-lista" aria-label="Vacinas">{saude?.vacinas.map((v, i) => <li key={i}><strong>{v.vacina}{v.dose ? ` ${v.dose}` : ''}</strong><span>{data(v.data)} · {v.unidade || 'Unidade não informada'}</span></li>)}</ul>
        ))}
        <h3>Consultas</h3>
        {miolo(l.saude, saude?.atendimentos.length ?? 0, carregada('consultas'), 'Nenhuma consulta registrada para você.', (
          <ul className="pc-lista" aria-label="Consultas">{saude?.atendimentos.map((a, i) => <li key={i}><strong>{a.especialidade || 'Atendimento'}</strong><span>{data(a.data)} · {a.unidade || 'Unidade não informada'}</span></li>)}</ul>
        ))}
        <h3>Consultas agendadas</h3>
        {miolo(l.saude, saude?.proximos.length ?? 0, carregada('agendamentos'), 'Nenhuma consulta agendada a partir de hoje.', (
          <ul className="pc-lista" aria-label="Consultas agendadas">{saude?.proximos.map((a, i) => <li key={i}><strong>{a.especialidade || 'Consulta'}</strong><span>{data(a.data)} · {a.unidade || 'Unidade não informada'}</span></li>)}</ul>
        ))}
        <p className="pc-nota">Diagnósticos e anotações clínicas não aparecem aqui.</p>
      </Area>
      <Area id="area-educacao" titulo="Educação">
        {miolo(l.educacao, mats.length, carregada('educacao'), 'Nenhuma matrícula na rede municipal para você.', (
          <ul className="pc-lista" aria-label="Matrículas">{mats.map((a, i) => (
            <li key={i}><strong>{a.matricula_atual ? [a.matricula_atual.escola || a.matricula_atual.unidade || 'Escola não informada', a.matricula_atual.turma].filter(Boolean).join(' · ') : 'Sem matrícula ativa'}</strong><span>{a.inativo ? 'Cadastro inativo' : 'Matrícula atual'}</span></li>
          ))}</ul>
        ))}
      </Area>
      <Area id="area-cemiterio" titulo="Cemitério">
        <h3>Túmulos</h3>
        {miolo(l.cemiterio, tumulos, carregada('cemiterio'), 'Nenhum túmulo ligado a você.', (
          <ul className="pc-lista" aria-label="Túmulos">
            {cem?.titular.map((t, i) => <li key={`t${i}`}><strong>{onde(t)}</strong><span>{t.ativo === false ? 'Foi titular' : 'Titular'}</span></li>)}
            {cem?.autorizado.map((t, i) => <li key={`a${i}`}><strong>{onde(t)}</strong><span>Autorizado{t.parentesco ? ` · ${t.parentesco}` : ''}</span></li>)}
            {cem?.leilao.map((t, i) => <li key={`l${i}`}><strong>{onde(t)}</strong><span>Comprado em leilão</span></li>)}
          </ul>
        ))}
        <h3>Sepultamento</h3>
        {miolo(l.cemiterio, cem?.sepultamento.length ?? 0, carregada('cemiterio'), 'Nenhuma sepultura vinculada a você.', (
          <ul className="pc-lista" aria-label="Sepultamento">{cem?.sepultamento.map((s, i) => <li key={i}><strong>{onde(s)}</strong></li>)}</ul>
        ))}
        <p className="pc-nota">Só aparecem os vínculos do seu cadastro.</p>
      </Area>
      <Area id="area-arvore" titulo="Árvore genealógica">
        {l.genealogia === 'falha' ? <p>Não foi possível ler esta área agora.</p>
          : l.genealogia !== 'ok' || !arv ? <p>Carregando…</p>
          : arv.estado === 'indisponivel' ? <p>Árvore genealógica indisponível.</p>
          : arv.parentes.length === 0 ? <p className="pc-vazio">Nenhum parente encontrado.</p>
          : <ul className="pc-lista" aria-label="Parentes">{arv.parentes.map((p, i) => (
            <li key={i} data-testid="parente"><strong>{p.nome}</strong><span>{p.parentesco}</span></li>
          ))}</ul>}
      </Area>
      <Area id="area-linha" titulo="Linha do tempo">
        {miolo(l.linha, vista.linha?.eventos.length ?? 0, l.linha === 'ok' ? true : undefined, 'Nenhum evento registrado.', (
          <ol className="pc-lista" aria-label="Linha do tempo">{vista.linha?.eventos.map((e, i) => (
            <li key={i}><strong>{e.titulo}</strong><span>{data(e.data)} · {e.area} · {e.fonte}</span></li>
          ))}</ol>
        ))}
      </Area>
    </div>
  )
}

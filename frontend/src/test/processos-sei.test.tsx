import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { salvaSessao } from '../api'
import { AuthProvider } from '../auth'
import ProcessosSei, { linkSeguro, type ProcessoSei as Proc } from '../ProcessosSei'

const CAMINHO = '/gold/cidadaos/00000000-0000-0000-0000-0000000000aa/sei'
const LINK = 'https://cidades.sei.sp.gov.br/franca/modulos/pesquisa/md_pesq_processo_pesquisar.php?acao_externa=protocolo_pesquisar&txtProtocoloPesquisa=99990.000101%2F2026-01'

function proc(i: number, extra: Partial<Proc> = {}): Proc {
  return {
    id_procedimento: `SF00010${i}`, protocolo: `99990.00010${i}/2026-01`, tipo_sei: 'ZZTESTE tipo livre',
    servico_carta: null, rotulo: 'ZZTESTE tipo livre', data_autuacao: '2026-09-02', unidade_atual: 'ZZUN',
    ultimo_andamento: { data_hora: '2026-10-03T09:00:00', descricao: 'ZZTESTE andamento' },
    status: 'Em andamento', documentos: 2, link: LINK.replace('000101', `00010${i}`), tem_outros: false, ...extra,
  }
}

const tres = {
  ficha_id: '00000000-0000-0000-0000-0000000000aa', fonte_liberada: true, mensagem: null, total: 3,
  processos: [
    proc(1, { tipo_sei: 'Autorização para corte de árvore', servico_carta: 'Corte de árvore', rotulo: 'Corte de árvore' }),
    proc(2),
    proc(3, { status: 'Concluído', rotulo: 'Certidão de área construída', servico_carta: 'Certidão de área construída', tipo_sei: 'Certidão de área construída (habite-se)' }),
  ],
}

function mockFetch(mapa: Record<string, [number, unknown]>) {
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const k = Object.keys(mapa).find((x) => String(u).includes(x))
    const [status, corpo] = k ? mapa[k] : [500, { detail: 'rota inesperada' }]
    return new Response(JSON.stringify(corpo), { status, headers: { 'Content-Type': 'application/json' } })
  }))
}

function abre(perfil = 'leitura') {
  salvaSessao({ token: 't', perfil, nome: 'ZZTESTE' })
  return render(<AuthProvider><ProcessosSei caminho={CAMINHO} /></AuthProvider>)
}

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('link da Pesquisa Pública', () => {
  it('SF5: só o host de Franca e sem documento ou nome na query', () => {
    expect(linkSeguro(LINK)).toContain('cidades.sei.sp.gov.br/franca')
    expect(linkSeguro(LINK)).toContain('99990.000101')
    expect(linkSeguro('https://cidades.sei.sp.gov.br/franca/x?cpf=000')).toBeNull()
    expect(linkSeguro('https://cidades.sei.sp.gov.br/franca/x?nome=ZZTESTE')).toBeNull()
    expect(linkSeguro('http://cidades.sei.sp.gov.br/franca/x')).toBeNull()
    expect(linkSeguro('https://exemplo.invalid/franca')).toBeNull()
  })
})

describe('bloco Processos SEI', () => {
  it('SF1: lista os três processos com os campos e o link', async () => {
    mockFetch({ [CAMINHO]: [200, tres] })
    abre()
    expect(await screen.findByRole('heading', { name: 'Corte de árvore' })).toBeVisible()
    expect(screen.getByText('Tipo no SEI: Autorização para corte de árvore')).toBeVisible()
    expect(screen.getByText('Protocolo: 99990.000101/2026-01')).toBeVisible()
    expect(screen.getAllByText(/Unidade atual: ZZUN/).length).toBe(3)
    expect(screen.getAllByText(/ZZTESTE andamento/).length).toBe(3)
    expect(screen.getByText('ZZTESTE tipo livre')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Certidão de área construída' })).toBeVisible()
    expect(screen.getAllByText('Em andamento').length).toBe(2)
    expect(screen.getByText('Concluído')).toBeVisible()
    expect(screen.getAllByText(/2 documentos/).length).toBe(3)
    const links = screen.getAllByRole('link', { name: 'Abrir no SEI' })
    expect(links).toHaveLength(3)
    for (const a of links) {
      const href = a.getAttribute('href') ?? ''
      expect(href).toContain('https://cidades.sei.sp.gov.br/franca')
      expect(href).not.toMatch(/cpf|cnpj|nome|ZZTESTE/i)
    }
    expect(screen.queryByText(/Carregando/)).toBeNull()
  })

  it('SF6: fonte travada e lista vazia, sem carregamento infinito', async () => {
    mockFetch({ [CAMINHO]: [200, { ficha_id: 'x', fonte_liberada: false, mensagem: 'Fonte SEI ainda não liberada', total: 0, processos: [] }] })
    const { unmount } = abre()
    expect(await screen.findByText('Fonte SEI ainda não liberada')).toBeVisible()
    expect(screen.queryByText(/Carregando/)).toBeNull()
    expect(screen.queryByRole('link', { name: 'Abrir no SEI' })).toBeNull()
    unmount()
    mockFetch({ [CAMINHO]: [200, { ficha_id: 'x', fonte_liberada: true, mensagem: 'Nenhum processo SEI', total: 0, processos: [] }] })
    abre()
    expect(await screen.findByText('Nenhum processo SEI')).toBeVisible()
    await waitFor(() => expect(screen.queryByText(/Carregando/)).toBeNull())
  })

  it('SF7: outros interessados mascarados; revelar pede motivo', async () => {
    const base = proc(1, { tem_outros: true })
    const corpo = { ficha_id: 'x', fonte_liberada: true, mensagem: null, total: 1, processos: [base] }
    const f = vi.fn(async (...args: [string, RequestInit?]) => {
      const url = String(args[0])
      if (url.endsWith('/interessados/revelar')) {
        return new Response(JSON.stringify({ interessados: [{ nome: 'ZZTESTE OUTRO INTERESSADO', cpf: 'ZZ-DOC', cnpj: null }], remascarar_em_segundos: 60 }), { status: 200 })
      }
      if (url.endsWith('/interessados')) {
        return new Response(JSON.stringify({ interessados: [{ nome: 'Z***** O**** I***********', cpf: '***.000.002-**', cnpj: null }] }), { status: 200 })
      }
      return new Response(JSON.stringify(corpo), { status: 200 })
    })
    vi.stubGlobal('fetch', f)
    abre('leitura')
    await userEvent.click(await screen.findByRole('button', { name: 'Ver outros interessados' }))
    expect(await screen.findByText(/Z\*\*\*\*\* O\*\*\*\*/)).toBeVisible()
    expect(screen.getByText(/\*\*\*\.000\.002-\*\*/)).toBeVisible()
    expect(screen.queryByText('ZZTESTE OUTRO INTERESSADO')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Revelar outros interessados' })).toBeNull()

    sessionStorage.clear()
    salvaSessao({ token: 't', perfil: 'admin_lgpd', nome: 'ZZTESTE' })
    render(<AuthProvider><ProcessosSei caminho={CAMINHO} /></AuthProvider>)
    const ver = await screen.findAllByRole('button', { name: 'Ver outros interessados' })
    await userEvent.click(ver[ver.length - 1])
    await userEvent.click(await screen.findByRole('button', { name: 'Revelar outros interessados' }))
    const revelar = screen.getByRole('button', { name: 'Revelar' })
    expect(revelar).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Motivo'), 'ZZTESTE conferir')
    expect(revelar).toBeEnabled()
    await userEvent.click(revelar)
    expect(await screen.findByText(/ZZTESTE OUTRO INTERESSADO/)).toBeVisible()
    const revelacao = f.mock.calls.map((c) => String(c[0])).filter((u) => u.includes('revelar'))
    expect(revelacao.length).toBe(1)
    const init = f.mock.calls.find((c) => String(c[0]).includes('revelar'))?.[1]
    expect(String(init?.body)).toContain('ZZTESTE conferir')
    expect(String(init?.body)).not.toMatch(/\d{11}/)
  })

  it('resposta sem o formato do bloco vira erro, sem quebrar a ficha', async () => {
    mockFetch({ [CAMINHO]: [200, { cidadao_id: 'c1', nome: 'ZZTESTE' }] })
    abre()
    expect(await screen.findByText(/Não foi possível ler os processos SEI agora/)).toBeVisible()
    expect(screen.queryByText(/Carregando/)).toBeNull()
  })
})

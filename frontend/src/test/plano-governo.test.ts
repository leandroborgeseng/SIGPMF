import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, waitFor, within } from '@testing-library/dom'
const source = readFileSync(resolve(process.cwd(), 'src/plano-governo/app.js'), 'utf8')
const mount = new Function(source.replace('export function mount(root)', 'return function mount(root)'))() as (root: HTMLElement) => () => void
const original = JSON.parse(readFileSync(resolve(process.cwd(), '../backend/app/plano_governo/seed.json'), 'utf8'))
let root: HTMLDivElement, dispose: (() => void) | undefined, conflict: boolean
let writes: { action: string; data: Record<string, unknown> }[]
let role: string
beforeEach(() => {
  window.location.hash = ''; conflict = false; writes = []; role = 'admin'
  sessionStorage.setItem('sigpmf_sessao', JSON.stringify({ token: 'jwt-test-only', perfil: 'admin', nome: 'Gestão' }))
  const state = structuredClone(original)
  state.departments = [{ id: 'EDUCACAO', name: 'Educação', leader: '' }, { id: 'SAUDE', name: 'Saúde', leader: '' }]
  root = document.createElement('div'); document.body.append(root)
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  vi.stubGlobal('scrollTo', vi.fn())
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/state')) return { ok: true, status: 200, json: async () => ({ state, user: { role, name: 'Gestão', email: 'admin@teste.local', departmentIds: [], allDepartments: true }, version: 0 }) }
    if (url.endsWith('/action')) { writes.push(JSON.parse(String(options?.body))); return { ok: !conflict, status: conflict ? 409 : 200, json: async () => conflict ? { detail: 'Conflito: recarregue o plano.' } : { ok: true } } }
    throw Error('Rota inesperada: ' + url)
  }))
})
afterEach(() => { dispose?.(); root.remove(); vi.unstubAllGlobals(); sessionStorage.clear() })
async function start() { dispose = mount(root); await waitFor(() => expect(within(root).getByText('O plano em movimento.')).toBeInTheDocument()) }
describe('Plano de Governo integrado', () => {
  it('usa JWT do SIGPMF e preserva os oito eixos sem validar automaticamente', async () => {
    await start()
    expect(fetch).toHaveBeenCalledWith('/api/plano-governo/state', expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer jwt-test-only' }) }))
    expect(root.textContent).toContain('49'); expect(root.querySelectorAll('.axis')).toHaveLength(8)
  })
  it('pesquisa compromissos e abre evidência documental', async () => {
    await start(); fireEvent.click(within(root).getByRole('link', { name: 'Compromissos' }))
    await waitFor(() => expect(root.querySelector('[data-filter="q"]')).toBeTruthy())
    fireEvent.input(root.querySelector('[data-filter="q"]')!, { target: { value: 'Autismo' } })
    await waitFor(() => expect(root.textContent).toContain('de 1 compromissos'))
    fireEvent.click(root.querySelector('button[data-action="open-commitment"]')!)
    expect(root.querySelector('dialog[open]')?.textContent).toContain('Pesquisa documental inicial')
  })
  it('envia projeto com secretaria e múltiplos compromissos', async () => {
    await start(); fireEvent.click(root.querySelector('button[data-action="new-project"]')!)
    const form = root.querySelector<HTMLFormElement>('#edit-form')!
    fireEvent.input(form.querySelector('[name="title"]')!, { target: { value: 'Escolas conectadas' } })
    fireEvent.change(form.querySelector('[name="departmentId"]')!, { target: { value: 'EDUCACAO' } })
    for (const id of ['13', '22']) fireEvent.click(form.querySelector(`input[name="commitmentIds"][value="${id}"]`)!)
    fireEvent.submit(form); await waitFor(() => expect(writes).toHaveLength(1))
    expect(writes[0]).toMatchObject({ action: 'project.save', data: { title: 'Escolas conectadas', departmentId: 'EDUCACAO', commitmentIds: [13, 22] } })
  })
  it('conflito mantém o formulário e a edição', async () => {
    await start(); conflict = true; fireEvent.click(root.querySelector('button[data-action="new-project"]')!)
    const form = root.querySelector<HTMLFormElement>('#edit-form')!
    fireEvent.input(form.querySelector('[name="title"]')!, { target: { value: 'Minha edição' } })
    fireEvent.submit(form); await waitFor(() => expect(form.querySelector('.form-error')?.textContent).toContain('recarregue'))
    expect(form.closest('dialog')).toHaveAttribute('open'); expect(form.querySelector<HTMLInputElement>('[name="title"]')!.value).toBe('Minha edição')
  })
  it('perfil leitura não recebe controles de alteração', async () => {
    role = 'leitura'; await start(); expect(root.querySelector('[data-action="new-project"]')).toBeNull(); expect(root.querySelector('a[href="#settings"]')).toBeNull()
  })
  it('mostra iniciativas documentais sem percentuais e permite alternar o Kanban', async () => {
    await start(); fireEvent.click(within(root).getByRole('link', { name: 'Quadro de projetos' }))
    await waitFor(() => expect(root.querySelectorAll('.board-group')).not.toHaveLength(0))
    expect(root.textContent).toContain('Avanço a validar')
    expect(root.querySelectorAll('.progress-number')).toHaveLength(0)
    fireEvent.click(within(root).getByRole('button', { name: /^Kanban$/ }))
    expect(root.querySelectorAll('.kanban-card')).toHaveLength(49)
  })
  it('compara períodos sem inventar medições a partir de notícias', async () => {
    await start(); fireEvent.click(within(root).getByRole('link', { name: 'Evolução semanal' }))
    await waitFor(() => expect(root.querySelector('#week-from')).toBeTruthy())
    expect(root.textContent).toContain('Sem duas medições')
    expect(root.textContent).toContain('Sem medição')
    expect(root.querySelectorAll('.progress-line')).toHaveLength(0)
  })

})

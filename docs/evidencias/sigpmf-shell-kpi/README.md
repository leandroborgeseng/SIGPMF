# Evidências — shell de painéis (onda 0)

Branch `factory/sigpmf-shell-kpi`. Tudo local: Postgres de teste (`sigpmf_shell_test` para o pytest e `sigpmf_shell_demo_test` para os prints e o SLA), dados **sintéticos** ZZTESTE, conta `@aion.local`. Sem deploy e sem agendamento ligado.

| Arquivo | O que mostra |
|---|---|
| `pytest-paineis-shell.txt` | 31 testes do backend do shell (P01–P08, S02, K01–K04), todos passando |
| `pytest-completo.txt` | Suíte completa do backend (resumo) |
| `vitest-shell-kpi.txt` | 8 testes do front do shell; suíte completa do front: 32 arquivos, 223 testes |
| `sla-p95.md` | S01: 4 endpoints, 5 chamadas de aquecimento e 20 medidas, com p95 bem abaixo de 2 s |
| `prints/375-*.png`, `prints/1280-*.png` | 9 estados × 2 larguras: ok, lacuna (jul/2026), drill Saúde, drill órgão, privacidade, vazio, sem fonte, erro e carregando |
| `prints-verificacao.json` | Por print: `scrollWidth` × `clientWidth` (sem rolagem horizontal) e axe-core WCAG 2 A/AA no Chrome, contraste incluído (0 violações) |

### A1 (UX) — barra inferior × cartão "Horas extras" em 375 px

Os `prints/375-*.png` são de página inteira: o Chrome desenha a `.nav-inferior` (fixa) na altura do viewport (812 px), por isso ela aparece "no meio" do cartão. Conferido com viewport real 375×812 e rolagem (`prints-375-viewport/`): rolando, o cartão "Horas extras" fica inteiro acima da barra, e no fim da página o último conteúdo termina em y=707 e a barra começa em y=748 (folga de 41 px, `.conteudo` com `padding-bottom: 88px` para a barra de 64 px). Não havia sobreposição real; CSS sem mudança.

| Arquivo | O que mostra |
|---|---|
| `prints-375-viewport/375-{ok,lacuna-jul,drill-saude,drill-orgao,privacidade}-cartao-horas-extras.png` | Viewport 375×812 rolado até o cartão "Horas extras" (centralizado) |
| `prints-375-viewport/375-{…}-fim.png` | Mesmo viewport rolado até o fim da página |
| `prints-375-viewport/medicao.txt` | Posições medidas (`navTop`, fundo do último conteúdo, `padding-bottom`) |

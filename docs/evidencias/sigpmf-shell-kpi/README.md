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

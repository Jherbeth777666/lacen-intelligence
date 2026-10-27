# Segurança e privacidade (LGPD)

Dados de laboratório são **dados pessoais sensíveis** (saúde). Os controles abaixo estão implementados.

## Separação operacional × analítico
| Camada | Contém | Quem acessa |
|---|---|---|
| `public.pacientes`, `triagens` | nome, CPF, CNS, endereço | só a aplicação; na interface, apenas os perfis Triagem/Admin |
| `public.agendamentos`, `atendimentos`, `atendimento_exames` | só `paciente_pseudo` (PAC_…) | aplicação |
| `analitico.*` (views) | só pseudônimos e agregados | dashboard, Analista IA, Power BI (papel `lacen_bi`) |

## Pseudonimização
- `PAC_` + HMAC-SHA256(segredo, identificador), truncado em 12 caracteres hexadecimais.
- O identificador segue a ordem: CPF válido → nome + data de nascimento → CNS → código de origem.
  Isso garante o **mesmo código** em agendamento, Worklab e triagem.
- Sem o segredo (`PSEUDO_SECRET`, fora do repositório) não dá para reverter o código nem recalculá-lo.
- O campo **Observações** dos agendamentos é **descartado** no ETL. No arquivo real ele continha
  comorbidades e números com formato de Cartão SUS.
- Os valores pessoais registrados nos logs de erro de importação são **mascarados** (`MAR***`).

## Aplicação
- Senhas com **bcrypt**, login no servidor, sessão com cookie HttpOnly/SameSite e expiração de 8 h.
- Limite de **5 tentativas de login** a cada 5 minutos por IP+e-mail. A verificação leva o mesmo tempo
  para e-mail existente ou não.
- **CSRF** em todos os POST (formulário e `X-CSRF-Token` no fetch).
- **Perfis**: admin, gestor, triagem, qualidade. O gestor vê a fila sem endereço; o perfil triagem não
  vê indicadores gerenciais.
- CPF e CNS sempre **mascarados** na tela (`***.982.247-**`). A busca por documento usa POST, para o
  número não aparecer em URL nem em log de acesso.
- **CSP** restritiva (só scripts locais), `X-Frame-Options`, `nosniff` e `no-store` nas páginas com
  dado pessoal.
- Saídas da IA renderizadas com **DOMPurify**. Dados de gráficos embutidos como JSON escapado.
- **Auditoria** por trigger em pacientes, triagens, NCs, planos, usuários, exames e UBS (CPF, CNS,
  endereço e hash de senha ficam fora do log).
- Cada pergunta à IA fica registrada em `ia_consultas`, com as ferramentas usadas e a base analisada.

## Banco (Supabase)
- `003_seguranca.sql` liga o **RLS em todas as tabelas** e revoga o acesso de `anon` e `authenticated`.
  A API REST pública do Supabase não lê nada. O projeto antigo usava a chave `anon` com tabelas
  abertas, o que expunha os CPFs.
- O backend conecta pela *connection string* (papel com política `app_total`). O Power BI usa o papel
  `lacen_bi`, que só lê `analitico`.
- No Supabase, **não exponha o schema `analitico`** na API (Settings → API → Exposed schemas).

## Checklist antes de usar dados reais
- [ ] Trocar a chave do Supabase e revogar a *service account* do Google que estavam no zip antigo
- [ ] `PSEUDO_SECRET` forte, guardado em cofre e diferente entre homologação e produção
- [ ] `SESSION_COOKIE_SECURE=1` e HTTPS
- [ ] `BASE_DEMO=0` e remover os usuários `@lacen.demo`
- [ ] Base de demonstração (fictícia) separada da base real

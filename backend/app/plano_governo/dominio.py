"""Plano agregado e versionado: ações validadas antes da gravação atômica.

Sem inferir cumprimento a partir da pesquisa. Secretarias e contas são as do SIGPMF.
O agregado limita a escala deste MVP ao plano 2025–2028; o compare-and-swap evita perda de atualização.
"""
import copy
import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


class PedidoInvalido(ValueError):
    pass


class SemPermissao(PermissionError):
    pass


def texto(valor, limite=5000):
    return str(valor or "").strip()[:limite]


def referencia(itens, identificador, tipo):
    item = next((x for x in itens if str(x["id"]) == str(identificador)), None)
    if item is None:
        raise PedidoInvalido(f"{tipo} não encontrado.")
    return item


def numero(valor, minimo=0, maximo=None):
    try:
        n = float(valor or 0)
    except (TypeError, ValueError):
        raise PedidoInvalido("Valor numérico inválido.") from None
    if not math.isfinite(n) or n < minimo or (maximo is not None and n > maximo):
        raise PedidoInvalido("Valor numérico fora do intervalo permitido.")
    return n


def data_iso(valor):
    if not valor:
        return ""
    try:
        resultado = date.fromisoformat(valor).isoformat()
        if resultado != valor:
            raise ValueError
        return resultado
    except (ValueError, TypeError):
        raise PedidoInvalido("Data inválida.") from None


def endereco(valor):
    s = texto(valor, 2000)
    if s and (urlparse(s).scheme not in ("http", "https") or not urlparse(s).netloc):
        raise PedidoInvalido("Use um endereço HTTP ou HTTPS válido para a evidência.")
    return s


def inicial(secretarias):
    s = json.loads(Path(__file__).with_name("seed.json").read_text())
    s["departments"] = secretarias
    existentes = {x["id"] for x in secretarias}
    for p in s["projects"]:
        if p["departmentId"] not in existentes:
            p["departmentId"] = ""
    for c in s["commitments"]:
        if c["departmentId"] not in existentes:
            c["departmentId"] = ""
    return s


def atualizar_base(estado, secretarias):
    """Importação documental idempotente: não apaga projetos, medições ou avaliações da gestão."""
    s = copy.deepcopy(estado)
    if not s.get("baseline"):
        base = inicial(secretarias)
        existentes = {p["id"] for p in s["projects"]}
        s["projects"].extend(p for p in base["projects"] if p["id"] not in existentes)
        ids = {e["id"] for e in s.get("documentaryEvents", [])}
        s.setdefault("documentaryEvents", []).extend(e for e in base["documentaryEvents"] if e["id"] not in ids)
        s["baseline"] = base["baseline"]
    s.setdefault("snapshots", [])
    s.setdefault("documentaryEvents", [])
    return s


def administrativo(u):
    return u["role"] == "admin"


def pode_projeto(u, p, s):
    if administrativo(u):
        return True
    return u["role"] == "secretaria" and (
        p.get("departmentId") in u["departmentIds"] or any(
            x["id"] == p.get("ownerId") and x.get("email", "").lower() == u["email"].lower()
            for x in s["people"]
        )
    )


def visivel(s, u):
    r = copy.deepcopy(s)
    if u["allDepartments"]:
        return r
    sec = set(u["departmentIds"])
    r["projects"] = [p for p in r["projects"] if p.get("departmentId") in sec or any(
        x["id"] == p.get("ownerId") and x.get("email", "").lower() == u["email"].lower() for x in s["people"])]
    pids = {p["id"] for p in r["projects"]}
    cids = {i for p in r["projects"] for i in p["commitmentIds"]}
    r["commitments"] = [c for c in r["commitments"] if c.get("departmentId") in sec or c["id"] in cids]
    cids = {str(c["id"]) for c in r["commitments"]}
    r["updates"] = [v for v in r["updates"] if v["projectId"] in pids]
    r["snapshots"] = [v for v in r.get("snapshots", []) if v["projectId"] in pids]
    r["documentaryEvents"] = [v for v in r.get("documentaryEvents", []) if v["projectId"] in pids]
    r["history"] = [h for h in r["history"] if h["subject"] in pids or (
        h["action"].startswith("commitment.") and h["subject"] in cids)]
    # O catálogo não contém dados pessoais; nomes de outras secretarias não concedem acesso a seus projetos.
    r["people"] = [p for p in r["people"] if p.get("departmentId") in sec or p["id"] in {
        pr.get("ownerId") for pr in r["projects"]} or p.get("email", "").lower() == u["email"].lower()]
    return r


def aplicar(estado, acao, dados, usuario, agora=None):
    s = copy.deepcopy(estado)
    agora = agora or datetime.now(UTC).isoformat()
    assunto = ""
    mensagem = ""
    if acao == "project.save":
        anterior = referencia(s["projects"], dados["id"], "Projeto") if dados.get("id") else None
        if usuario["role"] not in ("admin", "secretaria"):
            raise SemPermissao("Este perfil não pode alterar projetos.")
        if anterior and not pode_projeto(usuario, anterior, s):
            raise SemPermissao("Sem permissão para alterar este projeto.")
        departamento = texto(dados.get("departmentId"), 100)
        responsavel = texto(dados.get("ownerId"), 100)
        if not departamento and not responsavel:
            raise PedidoInvalido("Vincule uma secretaria ou um responsável ao projeto.")
        if departamento:
            referencia(s["departments"], departamento, "Secretaria ativa")
        if responsavel:
            referencia(s["people"], responsavel, "Responsável")
        if not pode_projeto(usuario, {"departmentId": departamento, "ownerId": responsavel}, s):
            raise SemPermissao("Vinculação fora do seu escopo de acesso.")
        titulo = texto(dados.get("title"), 200)
        if not titulo:
            raise PedidoInvalido("Informe o nome do projeto.")
        try:
            cids = list(dict.fromkeys(int(i) for i in dados.get("commitmentIds", [])))
        except (TypeError, ValueError):
            raise PedidoInvalido("Compromissos inválidos.") from None
        if not cids:
            raise PedidoInvalido("Vincule pelo menos um compromisso ao projeto.")
        permitidos = {c["id"] for c in visivel(s, usuario)["commitments"]}
        for cid in cids:
            referencia(s["commitments"], cid, "Compromisso")
            if cid not in permitidos:
                raise SemPermissao("Sem acesso a um dos compromissos selecionados.")
        inicio, fim = data_iso(dados.get("start")), data_iso(dados.get("due"))
        if inicio and fim and inicio > fim:
            raise PedidoInvalido("A conclusão não pode ser anterior ao início.")
        status = dados.get("status")
        if status not in ("em_validacao", "planejado", "em_andamento", "bloqueado", "concluido"):
            raise PedidoInvalido("Situação inválida.")
        for campo in ("milestones", "evidence"):
            itens = dados.get(campo, [])
            if not isinstance(itens, list) or len(itens) > 200 or any(not isinstance(x, dict) for x in itens):
                raise PedidoInvalido("Marcos ou evidências inválidos (máximo de 200).")
        marcos = []
        for m in dados.get("milestones", []):
            titulo_marco = texto(m.get("title"), 200)
            if not titulo_marco:
                raise PedidoInvalido("Cada marco precisa de um título.")
            dono_marco = texto(m.get("ownerId"), 100)
            if dono_marco:
                referencia(s["people"], dono_marco, "Responsável da tarefa")
            prazo_marco = data_iso(m.get("due"))
            if prazo_marco and ((inicio and prazo_marco < inicio) or (fim and prazo_marco > fim)):
                raise PedidoInvalido("O prazo da tarefa deve estar no período do projeto.")
            marcos.append({"id": texto(m.get("id"), 100) or str(uuid4()), "title": titulo_marco,
                           "due": prazo_marco, "ownerId": dono_marco, "done": m.get("done") is True,
                           "weight": numero(m.get("weight", 1), 0.01, 1000)})
        if len({m["id"] for m in marcos}) != len(marcos):
            raise PedidoInvalido("Marcos duplicados.")
        evidencias = [{**next((old for old in (anterior or {}).get("evidence", []) if old["id"] == e.get("id")), {}),
                       "id": texto(e.get("id"), 100) or str(uuid4()),
                       "title": texto(e.get("title"), 200) or "Evidência de entrega", "url": endereco(e.get("url"))}
                      for e in dados.get("evidence", []) if e.get("url")]
        progresso = None if dados.get("progress") in (None, "") else int(numero(dados.get("progress"), 0, 100))
        if marcos:
            progresso = round(100 * sum(m["weight"] for m in marcos if m["done"]) / sum(m["weight"] for m in marcos))
        if status == "concluido" and (progresso != 100 or not evidencias or any(not m["done"] for m in marcos)):
            raise PedidoInvalido("Para concluir, registre 100% de avanço e pelo menos uma evidência.")
        impedimento = texto(dados.get("blocker"), 2000)
        if status == "bloqueado" and not impedimento:
            raise PedidoInvalido("Descreva o impedimento do projeto bloqueado.")
        prioridade = dados.get("priority", "media")
        if prioridade not in ("alta", "media", "baixa"):
            raise PedidoInvalido("Prioridade inválida.")
        projeto = {**(anterior or {}), "id": anterior["id"] if anterior else str(uuid4()), "title": titulo,
                   "departmentId": departamento, "ownerId": responsavel, "commitmentIds": cids,
                   "status": status, "priority": prioridade, "start": inicio, "due": fim,
                   "progress": progresso, "budget": numero(dados.get("budget")), "spent": numero(dados.get("spent")),
                   "objective": texto(dados.get("objective")), "nextAction": texto(dados.get("nextAction"), 2000),
                   "blocker": impedimento, "milestones": marcos, "evidence": evidencias,
                   "archived": anterior.get("archived", False) if anterior else False,
                   "createdAt": anterior["createdAt"] if anterior else agora,
                   "updatedAt": agora, "updatedBy": usuario["name"]}
        if anterior:
            s["projects"][s["projects"].index(anterior)] = projeto
        else:
            s["projects"].append(projeto)
        s.setdefault("snapshots", []).append({
            "id": str(uuid4()), "projectId": projeto["id"], "date": agora, "author": usuario["name"],
            "progress": progresso, "status": status, "previousStatus": anterior.get("status") if anterior else None,
            "due": fim, "budget": projeto["budget"], "spent": projeto["spent"],
            "milestonesTotal": len(marcos), "milestonesDone": sum(m["done"] for m in marcos), "type": "gestao"})
        nota = texto(dados.get("updateNote"), 3000)
        if nota:
            s["updates"].insert(0, {"id": str(uuid4()), "projectId": projeto["id"], "text": nota,
                                    "date": agora, "author": usuario["name"], "progress": progresso})
        assunto = projeto["id"]
        mensagem = f"Projeto {'atualizado' if anterior else 'criado'}: {titulo}"
    elif acao == "project.archive":
        if not administrativo(usuario):
            raise SemPermissao("Somente o administrador pode arquivar projetos.")
        p = referencia(s["projects"], dados.get("id"), "Projeto")
        p["archived"] = not p.get("archived", False)
        p["updatedAt"] = agora
        assunto, mensagem = p["id"], f"Projeto {'arquivado' if p['archived'] else 'reativado'}: {p['title']}"
    elif acao == "commitment.save":
        if not administrativo(usuario) and usuario["role"] != "prefeito":
            raise SemPermissao("Somente a administração ou o gabinete pode validar compromissos.")
        c = referencia(s["commitments"], dados.get("id"), "Compromisso")
        validacao = dados.get("validation")
        if validacao not in ("a_validar", "planejada", "em_andamento", "parcial", "cumprida"):
            raise PedidoInvalido("Validação inválida.")
        nota, evidencia = texto(dados.get("validationNote"), 3000), endereco(dados.get("validationEvidence"))
        projetos = [p for p in s["projects"] if not p.get("archived") and c["id"] in p["commitmentIds"]]
        if validacao == "cumprida" and (not projetos or any(p["status"] != "concluido" for p in projetos)
                                        or not nota or not evidencia):
            raise PedidoInvalido("Conclua os projetos vinculados e registre justificativa e evidência antes de validar.")
        departamento = texto(dados.get("departmentId"), 100)
        if departamento:
            referencia(s["departments"], departamento, "Secretaria ativa")
        c.update(validation=validacao, validationNote=nota, validationEvidence=evidencia,
                 acceptanceCriteria=texto(dados.get("acceptanceCriteria")), departmentId=departamento,
                 updatedAt=agora, validatedBy=usuario["name"])
        assunto, mensagem = str(c["id"]), f"Compromisso #{c['id']}: avaliação atualizada ({validacao})"
    elif acao == "person.save":
        if not administrativo(usuario):
            raise SemPermissao("Somente o administrador pode cadastrar responsáveis.")
        anterior = referencia(s["people"], dados["id"], "Responsável") if dados.get("id") else None
        nome, email = texto(dados.get("name"), 150), texto(dados.get("email"), 200).lower()
        if not nome:
            raise PedidoInvalido("Informe o nome do responsável.")
        if email and ("@" not in email or any(c.isspace() for c in email)):
            raise PedidoInvalido("E-mail inválido.")
        departamento = texto(dados.get("departmentId"), 100)
        if departamento:
            referencia(s["departments"], departamento, "Secretaria ativa")
        pessoa = {"id": anterior["id"] if anterior else str(uuid4()), "name": nome, "email": email,
                  "departmentId": departamento, "position": texto(dados.get("position"), 150)}
        if anterior:
            s["people"][s["people"].index(anterior)] = pessoa
        else:
            s["people"].append(pessoa)
        assunto, mensagem = pessoa["id"], f"Responsável cadastrado/atualizado: {nome}"
    else:
        raise PedidoInvalido("Operação desconhecida.")
    # Uma iniciativa reaberta/adicionada ou o arquivamento da última iniciativa exige nova avaliação.
    for c in s["commitments"]:
        if c["validation"] == "cumprida":
            vinculados = [p for p in s["projects"] if not p.get("archived") and c["id"] in p["commitmentIds"]]
            if not vinculados or any(p["status"] != "concluido" for p in vinculados):
                c["validation"] = "a_validar"
                c["updatedAt"] = agora
                c["reviewReason"] = "Os projetos vinculados foram alterados; reavalie o cumprimento."
    s["history"].insert(0, {"id": str(uuid4()), "action": acao, "subject": assunto, "message": mensagem,
                            "actor": usuario["name"], "date": agora})
    return s

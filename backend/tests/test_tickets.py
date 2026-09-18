from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings
from conftest import auth_headers


def ticket_payload(title: str = "Erro no sistema") -> dict[str, object]:
    return {
        "title": title,
        "description": "Descricao detalhada do problema encontrado.",
        "priority": "MEDIA",
        "category_id": 1,
        "sector_id": 1,
        "support_area_id": 1,
        "support_type_id": 1,
    }


def create_ticket(client: TestClient, headers: dict[str, str], title: str = "Erro no sistema") -> dict[str, object]:
    response = client.post("/api/tickets", json=ticket_payload(title), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def create_user(
    client: TestClient,
    headers: dict[str, str],
    email: str,
    role: str,
    password: str,
) -> dict[str, object]:
    response = client.post(
        "/api/users",
        json={
            "name": "Usuario Comentarios",
            "email": email,
            "password": password,
            "role": role,
            "is_active": True,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_attachment(
    client: TestClient,
    ticket_id: int,
    headers: dict[str, str],
    filename: str = "evidencia.png",
) -> object:
    return client.post(
        f"/api/tickets/{ticket_id}/attachments",
        files={"files": (filename, b"conteudo-do-anexo", "image/png")},
        headers=headers,
    )


def test_requester_can_create_ticket(client: TestClient) -> None:
    headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")

    response = client.post("/api/tickets", json=ticket_payload(), headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Erro no sistema"
    assert body["status"] == "ABERTO"
    assert body["requester"]["email"] == "solicitante@example.com"


def test_technician_cannot_create_ticket(client: TestClient) -> None:
    headers = auth_headers(client, "tecnico@example.com", "TecnicoTest@123")

    response = client.post("/api/tickets", json=ticket_payload(), headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Tecnico nao pode criar chamados."


def test_inactive_category_cannot_be_used(client: TestClient) -> None:
    headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    payload = ticket_payload()
    payload["category_id"] = 2

    response = client.post("/api/tickets", json=payload, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Categoria inativa nao pode ser usada."


def test_ticket_listing_is_paginated_and_scoped_by_requester(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    other_headers = auth_headers(client, "outro@example.com", "OutroTest@123")
    for index in range(12):
        create_ticket(client, requester_headers, f"Chamado paginado {index}")
    create_ticket(client, other_headers, "Chamado de outro solicitante")

    response = client.get("/api/tickets?page=2&per_page=5", headers=requester_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 12
    assert body["page"] == 2
    assert body["per_page"] == 5
    assert body["total_pages"] == 3
    assert len(body["items"]) == 5
    assert all(item["requester"]["email"] == "solicitante@example.com" for item in body["items"])


def test_admin_assigns_and_technician_updates_ticket(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    admin_headers = auth_headers(client, "admin@example.com", "AdminTest@123")
    technician_headers = auth_headers(client, "tecnico@example.com", "TecnicoTest@123")
    ticket = create_ticket(client, requester_headers)

    assign_response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"assignee_id": 2},
        headers=admin_headers,
    )
    assert assign_response.status_code == 200
    assert assign_response.json()["assignee"]["email"] == "tecnico@example.com"

    status_response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"status": "EM_ANDAMENTO"},
        headers=technician_headers,
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "EM_ANDAMENTO"

    priority_response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"priority": "ALTA"},
        headers=admin_headers,
    )
    assert priority_response.status_code == 200
    assert priority_response.json()["priority"] == "ALTA"


def test_valid_status_transition_keeps_existing_audit_behavior(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    admin_headers = auth_headers(client, "admin@example.com", "AdminTest@123")
    ticket = create_ticket(client, requester_headers)

    response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"status": "EM_ANDAMENTO"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "EM_ANDAMENTO"

    detail_response = client.get(f"/api/tickets/{ticket['id']}", headers=admin_headers)
    assert detail_response.status_code == 200
    audits = detail_response.json()["audits"]
    status_audits = [audit for audit in audits if audit["action"] == "STATUS_ALTERADO"]
    assert len(status_audits) == 1
    assert status_audits[0]["old_value"] == "ABERTO"
    assert status_audits[0]["new_value"] == "EM_ANDAMENTO"


def test_invalid_status_transition_is_rejected(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    admin_headers = auth_headers(client, "admin@example.com", "AdminTest@123")
    ticket = create_ticket(client, requester_headers)

    response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"status": "CONCLUIDO"},
        headers=admin_headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Transicao de status invalida: ABERTO para CONCLUIDO."


def test_invalid_status_transition_does_not_mutate_ticket_or_audit(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    admin_headers = auth_headers(client, "admin@example.com", "AdminTest@123")
    ticket = create_ticket(client, requester_headers)

    response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"status": "CONCLUIDO"},
        headers=admin_headers,
    )
    assert response.status_code == 400

    detail_response = client.get(f"/api/tickets/{ticket['id']}", headers=admin_headers)
    assert detail_response.status_code == 200
    body = detail_response.json()
    assert body["status"] == "ABERTO"
    assert body["resolved_at"] is None
    assert body["closed_at"] is None
    assert [audit["action"] for audit in body["audits"]] == ["CHAMADO_CRIADO"]


def test_terminal_ticket_status_cannot_change(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    admin_headers = auth_headers(client, "admin@example.com", "AdminTest@123")
    ticket = create_ticket(client, requester_headers)

    in_progress_response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"status": "EM_ANDAMENTO"},
        headers=admin_headers,
    )
    assert in_progress_response.status_code == 200
    concluded_response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"status": "CONCLUIDO"},
        headers=admin_headers,
    )
    assert concluded_response.status_code == 200

    response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"status": "EM_ANDAMENTO"},
        headers=admin_headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Transicao de status invalida: CONCLUIDO para EM_ANDAMENTO."

    detail_response = client.get(f"/api/tickets/{ticket['id']}", headers=admin_headers)
    assert detail_response.status_code == 200
    body = detail_response.json()
    assert body["status"] == "CONCLUIDO"
    assert body["resolved_at"] is not None
    assert body["closed_at"] is not None
    assert len([audit for audit in body["audits"] if audit["action"] == "STATUS_ALTERADO"]) == 2


def test_ticket_owner_can_comment_and_authorized_user_can_view_comment(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    admin_headers = auth_headers(client, "admin@example.com", "AdminTest@123")
    ticket = create_ticket(client, requester_headers)

    response = client.post(
        f"/api/tickets/{ticket['id']}/comments",
        json={"message": "Comentario do solicitante dono."},
        headers=requester_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["message"] == "Comentario do solicitante dono."
    assert body["author"]["email"] == "solicitante@example.com"

    detail_response = client.get(f"/api/tickets/{ticket['id']}", headers=admin_headers)
    assert detail_response.status_code == 200
    comments = detail_response.json()["comments"]
    assert len(comments) == 1
    assert comments[0]["message"] == "Comentario do solicitante dono."
    assert comments[0]["author"]["email"] == "solicitante@example.com"


def test_other_requester_cannot_comment_on_someone_elses_ticket(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    other_headers = auth_headers(client, "outro@example.com", "OutroTest@123")
    ticket = create_ticket(client, requester_headers)

    response = client.post(
        f"/api/tickets/{ticket['id']}/comments",
        json={"message": "Tentativa de comentario indevida."},
        headers=other_headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Chamado nao encontrado."

    detail_response = client.get(f"/api/tickets/{ticket['id']}", headers=requester_headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["comments"] == []


def test_unassigned_technician_cannot_comment_on_ticket_assigned_to_another_technician(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    admin_headers = auth_headers(client, "admin@example.com", "AdminTest@123")
    ticket = create_ticket(client, requester_headers)
    other_technician = create_user(
        client,
        admin_headers,
        "tecnico2@example.com",
        "TECNICO",
        "Tecnico2Test@123",
    )

    assign_response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"assignee_id": 2},
        headers=admin_headers,
    )
    assert assign_response.status_code == 200

    other_technician_headers = auth_headers(client, other_technician["email"], "Tecnico2Test@123")
    response = client.post(
        f"/api/tickets/{ticket['id']}/comments",
        json={"message": "Tentativa de comentario tecnico indevida."},
        headers=other_technician_headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Chamado nao encontrado."

    detail_response = client.get(f"/api/tickets/{ticket['id']}", headers=admin_headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["comments"] == []


def test_authorized_user_can_upload_and_download_ticket_attachment(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    admin_headers = auth_headers(client, "admin@example.com", "AdminTest@123")
    ticket = create_ticket(client, requester_headers)

    response = upload_attachment(client, ticket["id"], requester_headers)

    assert response.status_code == 201
    attachment = response.json()[0]
    assert attachment["original_filename"] == "evidencia.png"
    assert attachment["content_type"] == "image/png"
    assert attachment["uploaded_by"]["email"] == "solicitante@example.com"

    detail_response = client.get(f"/api/tickets/{ticket['id']}", headers=admin_headers)
    assert detail_response.status_code == 200
    attachments = detail_response.json()["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["id"] == attachment["id"]
    assert attachments[0]["original_filename"] == "evidencia.png"

    download_response = client.get(
        f"/api/tickets/{ticket['id']}/attachments/{attachment['id']}",
        headers=admin_headers,
    )
    assert download_response.status_code == 200
    assert download_response.content == b"conteudo-do-anexo"
    assert len(list(tmp_path.iterdir())) == 1


def test_other_requester_cannot_upload_or_download_ticket_attachment(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    other_headers = auth_headers(client, "outro@example.com", "OutroTest@123")
    admin_headers = auth_headers(client, "admin@example.com", "AdminTest@123")
    ticket = create_ticket(client, requester_headers)

    denied_upload = upload_attachment(client, ticket["id"], other_headers)

    assert denied_upload.status_code == 404
    assert denied_upload.json()["error"]["message"] == "Chamado nao encontrado."
    assert list(tmp_path.iterdir()) == []

    allowed_upload = upload_attachment(client, ticket["id"], requester_headers)
    assert allowed_upload.status_code == 201
    attachment = allowed_upload.json()[0]

    denied_download = client.get(
        f"/api/tickets/{ticket['id']}/attachments/{attachment['id']}",
        headers=other_headers,
    )
    assert denied_download.status_code == 404
    assert denied_download.json()["error"]["message"] == "Chamado nao encontrado."

    detail_response = client.get(f"/api/tickets/{ticket['id']}", headers=admin_headers)
    assert detail_response.status_code == 200
    assert len(detail_response.json()["attachments"]) == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_technician_without_visibility_cannot_upload_or_download_ticket_attachment(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    admin_headers = auth_headers(client, "admin@example.com", "AdminTest@123")
    ticket = create_ticket(client, requester_headers)
    other_technician = create_user(
        client,
        admin_headers,
        "tecnico2@example.com",
        "TECNICO",
        "Tecnico2Test@123",
    )

    assign_response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"assignee_id": 2},
        headers=admin_headers,
    )
    assert assign_response.status_code == 200

    other_technician_headers = auth_headers(client, other_technician["email"], "Tecnico2Test@123")
    denied_upload = upload_attachment(client, ticket["id"], other_technician_headers)

    assert denied_upload.status_code == 404
    assert denied_upload.json()["error"]["message"] == "Chamado nao encontrado."
    assert list(tmp_path.iterdir()) == []

    allowed_upload = upload_attachment(client, ticket["id"], requester_headers)
    assert allowed_upload.status_code == 201
    attachment = allowed_upload.json()[0]

    denied_download = client.get(
        f"/api/tickets/{ticket['id']}/attachments/{attachment['id']}",
        headers=other_technician_headers,
    )
    assert denied_download.status_code == 404
    assert denied_download.json()["error"]["message"] == "Chamado nao encontrado."

    detail_response = client.get(f"/api/tickets/{ticket['id']}", headers=admin_headers)
    assert detail_response.status_code == 200
    assert len(detail_response.json()["attachments"]) == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_requester_cannot_update_status(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")
    ticket = create_ticket(client, requester_headers)

    response = client.put(
        f"/api/tickets/{ticket['id']}",
        json={"status": "CONCLUIDO"},
        headers=requester_headers,
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Solicitante nao pode alterar este campo."


def test_ticket_create_validates_payload(client: TestClient) -> None:
    requester_headers = auth_headers(client, "solicitante@example.com", "SolicitanteTest@123")

    response = client.post(
        "/api/tickets",
        json={
            "title": "Oi",
            "description": "curta",
            "priority": "MEDIA",
            "category_id": 1,
            "sector_id": 1,
            "support_area_id": 1,
            "support_type_id": 1,
        },
        headers=requester_headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Erro de validacao dos dados enviados."

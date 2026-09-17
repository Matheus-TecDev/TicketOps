from datetime import datetime, timezone
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import TicketStatus, UserRole
from app.models.attachment import TicketAttachment
from app.models.audit import TicketAudit
from app.models.category import Category
from app.models.comment import TicketComment
from app.models.support import Sector, SupportArea, SupportType
from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.ticket import TicketCommentCreate, TicketCreate, TicketUpdate

logger = logging.getLogger(__name__)
ALLOWED_ATTACHMENT_PREFIXES = ("image/", "video/")
ALLOWED_STATUS_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.ABERTO: {TicketStatus.EM_ANDAMENTO, TicketStatus.CANCELADO},
    TicketStatus.EM_ANDAMENTO: {
        TicketStatus.AGUARDANDO_SOLICITANTE,
        TicketStatus.AGUARDANDO_TERCEIROS,
        TicketStatus.CONCLUIDO,
        TicketStatus.CANCELADO,
    },
    TicketStatus.AGUARDANDO_SOLICITANTE: {
        TicketStatus.EM_ANDAMENTO,
        TicketStatus.CONCLUIDO,
        TicketStatus.CANCELADO,
    },
    TicketStatus.AGUARDANDO_TERCEIROS: {
        TicketStatus.EM_ANDAMENTO,
        TicketStatus.CONCLUIDO,
        TicketStatus.CANCELADO,
    },
    TicketStatus.CONCLUIDO: set(),
    TicketStatus.CANCELADO: set(),
}


def assert_category_exists(db: Session, category_id: int) -> Category:
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoria nao encontrada.")
    if not category.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Categoria inativa nao pode ser usada.")
    return category


def get_default_category(db: Session) -> Category:
    category = db.scalar(select(Category).where(Category.name == "OUTROS"))
    if category and category.is_active:
        return category
    if category and not category.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Categoria padrao OUTROS esta inativa.")
    category = Category(name="OUTROS", description="Categoria legada para chamados do novo fluxo.")
    db.add(category)
    db.flush()
    return category


def assert_sector_is_active(db: Session, sector_id: int) -> Sector:
    sector = db.get(Sector, sector_id)
    if not sector:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setor nao encontrado.")
    if not sector.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setor esta inativo.")
    return sector


def assert_support_context_is_active(db: Session, support_area_id: int, support_type_id: int) -> tuple[SupportArea, SupportType]:
    support_area = db.get(SupportArea, support_area_id)
    if not support_area:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Area de suporte nao encontrada.")
    if not support_area.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Area de suporte esta inativa.")

    support_type = db.get(SupportType, support_type_id)
    if not support_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tipo de suporte nao encontrado.")
    if not support_type.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tipo de suporte esta inativo.")
    if support_type.support_area_id != support_area.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tipo de suporte nao pertence a area selecionada.",
        )
    return support_area, support_type


def assert_can_access(ticket: Ticket | None) -> Ticket:
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chamado nao encontrado.")
    return ticket


def assert_assignee_is_active_technician(db: Session, assignee_id: int | None) -> None:
    if assignee_id is None:
        return
    assignee = db.get(User, assignee_id)
    if not assignee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tecnico responsavel nao encontrado.")
    if not assignee.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tecnico responsavel esta inativo.")
    if assignee.role != UserRole.TECNICO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Responsavel pelo chamado deve ter perfil TECNICO.",
        )


def assert_status_transition_allowed(current_status: TicketStatus, new_status: TicketStatus) -> None:
    if new_status not in ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Transicao de status invalida: {current_status} para {new_status}.",
        )


def create_audit(
    db: Session,
    ticket: Ticket,
    actor: User,
    action: str,
    field_name: str | None = None,
    old_value: object | None = None,
    new_value: object | None = None,
) -> None:
    db.add(
        TicketAudit(
            ticket=ticket,
            actor_id=actor.id,
            action=action,
            field_name=field_name,
            old_value=None if old_value is None else str(old_value),
            new_value=None if new_value is None else str(new_value),
        )
    )


def create_ticket(db: Session, payload: TicketCreate, actor: User) -> Ticket:
    if actor.role == UserRole.TECNICO:
        logger.warning("Technician attempted to create ticket", extra={"actor_id": actor.id})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tecnico nao pode criar chamados.")
    category = assert_category_exists(db, payload.category_id) if payload.category_id else get_default_category(db)
    assert_sector_is_active(db, payload.sector_id)
    support_area, support_type = assert_support_context_is_active(db, payload.support_area_id, payload.support_type_id)
    title = payload.title or f"{support_area.name} - {support_type.name}"
    ticket = Ticket(
        title=title[:180],
        description=payload.description,
        priority=payload.priority,
        category_id=category.id,
        sector_id=payload.sector_id,
        support_area_id=payload.support_area_id,
        support_type_id=payload.support_type_id,
        requester_id=actor.id,
    )
    db.add(ticket)
    db.flush()
    create_audit(db, ticket, actor, "CHAMADO_CRIADO")
    db.commit()
    db.refresh(ticket)
    logger.info(
        "Ticket created",
        extra={"ticket_id": ticket.id, "actor_id": actor.id, "category_id": ticket.category_id, "priority": ticket.priority.value},
    )
    return ticket


def update_ticket(db: Session, ticket: Ticket, payload: TicketUpdate, actor: User) -> Ticket:
    values = payload.model_dump(exclude_unset=True)
    if actor.role == UserRole.SOLICITANTE and set(values) - {
        "title",
        "description",
        "priority",
        "category_id",
        "sector_id",
        "support_area_id",
        "support_type_id",
    }:
        logger.warning("Requester attempted forbidden ticket update", extra={"ticket_id": ticket.id, "actor_id": actor.id})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solicitante nao pode alterar este campo.")
    if actor.role == UserRole.TECNICO:
        allowed_fields = {"status", "assignee_id"}
        if set(values) - allowed_fields:
            logger.warning("Technician attempted forbidden ticket update", extra={"ticket_id": ticket.id, "actor_id": actor.id})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tecnico nao pode alterar este campo.")
        if "assignee_id" in values and (ticket.assignee_id is not None or values["assignee_id"] != actor.id):
            logger.warning("Technician attempted invalid ticket assignment", extra={"ticket_id": ticket.id, "actor_id": actor.id})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tecnico so pode assumir chamados sem responsavel.")
    if "category_id" in values and values["category_id"] is not None:
        assert_category_exists(db, values["category_id"])
    if "sector_id" in values and values["sector_id"] is not None:
        assert_sector_is_active(db, values["sector_id"])
    if "support_area_id" in values or "support_type_id" in values:
        support_area_id = values.get("support_area_id") or ticket.support_area_id
        support_type_id = values.get("support_type_id") or ticket.support_type_id
        assert_support_context_is_active(db, support_area_id, support_type_id)
    if "assignee_id" in values:
        assert_assignee_is_active_technician(db, values["assignee_id"])

    values = {field: value for field, value in values.items() if value is not None or field == "assignee_id"}
    if "status" in values and ticket.status != values["status"]:
        assert_status_transition_allowed(ticket.status, values["status"])

    for field, value in values.items():
        old_value = getattr(ticket, field)
        if old_value == value:
            continue
        setattr(ticket, field, value)
        action = "STATUS_ALTERADO" if field == "status" else "CAMPO_ATUALIZADO"
        create_audit(db, ticket, actor, action, field, old_value, value)

    now = datetime.now(timezone.utc)
    if ticket.status == TicketStatus.CONCLUIDO and ticket.resolved_at is None:
        ticket.resolved_at = now
    if ticket.status in {TicketStatus.CONCLUIDO, TicketStatus.CANCELADO} and ticket.closed_at is None:
        ticket.closed_at = now

    db.commit()
    db.refresh(ticket)
    logger.info("Ticket updated", extra={"ticket_id": ticket.id, "actor_id": actor.id, "fields": sorted(values)})
    return ticket


def add_comment(db: Session, ticket: Ticket, payload: TicketCommentCreate, actor: User) -> TicketComment:
    comment = TicketComment(ticket_id=ticket.id, author_id=actor.id, message=payload.message)
    db.add(comment)
    create_audit(db, ticket, actor, "COMENTARIO_ADICIONADO")
    db.commit()
    db.refresh(comment)
    logger.info("Ticket comment added", extra={"ticket_id": ticket.id, "actor_id": actor.id, "comment_id": comment.id})
    return comment


async def add_attachments(db: Session, ticket: Ticket, files: list[UploadFile], actor: User) -> list[TicketAttachment]:
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nenhum anexo enviado.")

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    attachments: list[TicketAttachment] = []

    for file in files:
        content_type = file.content_type or "application/octet-stream"
        if not content_type.startswith(ALLOWED_ATTACHMENT_PREFIXES):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Apenas imagens e videos sao permitidos como anexos.",
            )

        content = await file.read(settings.MAX_ATTACHMENT_SIZE_BYTES + 1)
        if len(content) > settings.MAX_ATTACHMENT_SIZE_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Anexo excede 25 MB.")
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Anexo vazio nao e permitido.")

        suffix = Path(file.filename or "").suffix.lower()[:16]
        stored_filename = f"{uuid4().hex}{suffix}"
        (upload_dir / stored_filename).write_bytes(content)
        attachment = TicketAttachment(
            ticket_id=ticket.id,
            uploaded_by_id=actor.id,
            original_filename=file.filename or "anexo",
            stored_filename=stored_filename,
            content_type=content_type,
            size_bytes=len(content),
        )
        db.add(attachment)
        attachments.append(attachment)

    create_audit(db, ticket, actor, "ANEXO_ADICIONADO")
    db.commit()
    for attachment in attachments:
        db.refresh(attachment)
    logger.info("Ticket attachments added", extra={"ticket_id": ticket.id, "actor_id": actor.id, "count": len(attachments)})
    return attachments


def get_attachment(db: Session, ticket: Ticket, attachment_id: int) -> TicketAttachment:
    attachment = db.get(TicketAttachment, attachment_id)
    if not attachment or attachment.ticket_id != ticket.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anexo nao encontrado.")
    return attachment


def get_attachment_path(attachment: TicketAttachment) -> Path:
    path = Path(settings.UPLOAD_DIR) / attachment.stored_filename
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arquivo do anexo nao encontrado.")
    return path

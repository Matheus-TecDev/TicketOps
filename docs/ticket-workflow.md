# Ticket Workflow

## Creation

`ADMIN` and `SOLICITANTE` users may create tickets. The `TECNICO` role is not allowed to perform this operation.

Ticket creation validates:

- active category;
- active department;
- active support area;
- active support type belonging to the selected area;
- priority and description.

When no category is provided, the application uses `OUTROS`. The initial status is `ABERTO`, and the audit trail records `CHAMADO_CRIADO`.

## States

- `ABERTO`;
- `EM_ANDAMENTO`;
- `AGUARDANDO_SOLICITANTE`;
- `AGUARDANDO_TERCEIROS`;
- `CONCLUIDO`;
- `CANCELADO`.

The service accepts status changes according to role permissions and validates the workflow transition:

- `ABERTO` -> `EM_ANDAMENTO`, `CANCELADO`;
- `EM_ANDAMENTO` -> `AGUARDANDO_SOLICITANTE`, `AGUARDANDO_TERCEIROS`, `CONCLUIDO`, `CANCELADO`;
- `AGUARDANDO_SOLICITANTE` -> `EM_ANDAMENTO`, `CONCLUIDO`, `CANCELADO`;
- `AGUARDANDO_TERCEIROS` -> `EM_ANDAMENTO`, `CONCLUIDO`, `CANCELADO`;
- `CONCLUIDO` is terminal;
- `CANCELADO` is terminal.

When a ticket reaches `CONCLUIDO`, `resolved_at` is populated. When it reaches `CONCLUIDO` or `CANCELADO`, `closed_at` is populated. These timestamps are not recalculated after being set.

## Assignment

- The assignee must be an active technician.
- Technicians can view unassigned tickets and tickets assigned to them.
- A technician may claim only an unassigned ticket and only for themselves.
- Administrators may manage assignments without those restrictions.

## Comments

Any user with access to a ticket may add comments. The operation creates a `TicketComment` and the `COMENTARIO_ADICIONADO` audit event.

## Attachments

Any user with access to a ticket may upload images or videos. Each file:

1. is validated by content type and size;
2. receives an internal UUID-based name;
3. is stored in the uploads volume;
4. has its metadata persisted in the database.

The operation creates the `ANEXO_ADICIONADO` audit event.

## Auditing

The `ticket_audits` table records:

- ticket;
- actor;
- action;
- changed field;
- previous value;
- new value;
- timestamp.

Status changes use `STATUS_ALTERADO`; other changes use `CAMPO_ATUALIZADO`.

## Search and Dashboard

Ticket lists support combinable filters and pagination. The dashboard uses the same visibility rules, preventing users from seeing aggregates for tickets they would not be allowed to query.

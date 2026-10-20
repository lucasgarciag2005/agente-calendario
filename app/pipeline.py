"""El flujo del agente: leer el mail, proponer, agendar con tu OK, recordar."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select, update

from app import calendar_client, gmail_client, privacy, security, telegram
from app.config import settings
from app.db import session_scope
from app.llm import ChatEvent, EmailAnalysis, LLMRefusal, analyze_email
from app.models import Proposal, SeenMessage
from app.store import active_topics, kv_get, kv_set

log = logging.getLogger(__name__)

# A cuantos dias de la fecha limite te avisa.
REMINDER_OFFSETS = [7, 3, 1, 0]

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MESES = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]


# --------------------------------------------------------------------------- #
# Fechas
# --------------------------------------------------------------------------- #


def parse_dt(date_str: str, time_str: str = "") -> dt.datetime | None:
    """'2026-12-15' + '18:30' -> datetime con la zona horaria local."""
    if not date_str:
        return None
    try:
        day = dt.date.fromisoformat(date_str.strip())
    except ValueError:
        log.warning("Fecha invalida del modelo: %r", date_str)
        return None

    hour, minute = 9, 0
    if time_str:
        try:
            parts = time_str.strip().split(":")
            hour, minute = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            log.warning("Hora invalida del modelo: %r", time_str)

    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=settings.tz)


def human_date(value: dt.datetime, with_time: bool = False) -> str:
    local = value.astimezone(settings.tz)
    text = f"{DIAS[local.weekday()]} {local.day} {MESES[local.month - 1]} {local.year}"
    if with_time:
        text += f" a las {local:%H:%M}"
    return text


def build_rrule(days: list[str], until: dt.date | None) -> str:
    rule = "RRULE:FREQ=WEEKLY;BYDAY=" + ",".join(days)
    if until:
        end = dt.datetime.combine(until, dt.time(23, 59), tzinfo=settings.tz)
        rule += ";UNTIL=" + end.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return rule


# --------------------------------------------------------------------------- #
# Revisar el mail
# --------------------------------------------------------------------------- #


def poll_emails() -> dict[str, int]:
    """Busca mails nuevos de los temas vigilados y propone lo que encuentra."""
    stats = {
        "candidatos": 0, "nuevos": 0, "privados": 0,
        "relevantes": 0, "propuestas": 0, "fallidos": 0, "sospechosos": 0,
    }

    with session_scope() as s:
        topics = active_topics(s)
        cursor = kv_get(s, "last_poll_epoch")

    if not topics:
        log.info("No hay temas activos, no reviso nada.")
        return stats

    if cursor:
        after = int(cursor)
    else:
        after = int(
            (
                dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(days=settings.initial_lookback_days)
            ).timestamp()
        )

    keywords = sorted({k for t in topics for k in (t.keywords or [])})
    senders = sorted({sd for t in topics for sd in (t.senders or [])})
    query = gmail_client.build_query(keywords, senders, after)
    log.info("Consulta a Gmail: %s", query)

    ids = gmail_client.list_message_ids(query)
    stats["candidatos"] = len(ids)

    # Movemos el cursor con un solapamiento de una hora: los duplicados ya los
    # filtra seen_messages, pero asi no se nos escapa nada en el borde.
    new_cursor = int(dt.datetime.now(dt.timezone.utc).timestamp()) - 3600

    for gmail_id in ids:
        with session_scope() as s:
            if s.get(SeenMessage, gmail_id) is not None:
                continue

        try:
            email = gmail_client.get_email(gmail_id)
        except Exception:
            log.exception("No pude leer el mail %s", gmail_id)
            stats["fallidos"] += 1
            continue

        stats["nuevos"] += 1

        # Primero de todo: si un mail que habla de plata no pasa la
        # verificacion del remitente, avisar y no procesarlo. No queremos que
        # un mail posiblemente falsificado te meta un pago en el calendario.
        veredicto = security.evaluar(email.auth_results, email.subject, email.body)
        if veredicto.alerta:
            _notify_suspicious(email, veredicto)
            with session_scope() as s:
                s.add(
                    SeenMessage(
                        gmail_id=email.gmail_id,
                        thread_id=email.thread_id,
                        subject=email.subject,
                        sender=email.sender,
                        internal_date=email.date,
                        relevant=True,
                        summary=f"(no analizado: autenticacion {veredicto.resumen()})",
                        analysis=None,
                    )
                )
            stats["sospechosos"] += 1
            continue

        # Antes de mandar nada al modelo, ver hasta donde puede viajar este
        # remitente. Con NOTIFY_ONLY el cuerpo no sale de la maquina.
        mode = privacy.mode_for(email.sender)
        if mode == privacy.NOTIFY_ONLY:
            _notify_private(email)
            with session_scope() as s:
                s.add(
                    SeenMessage(
                        gmail_id=email.gmail_id,
                        thread_id=email.thread_id,
                        subject=email.subject,
                        sender=email.sender,
                        internal_date=email.date,
                        relevant=True,
                        summary="(no analizado: remitente privado)",
                        analysis=None,
                    )
                )
            stats["privados"] += 1
            continue

        texto = (
            email.as_prompt_text() if mode == privacy.FULL else email.as_subject_only()
        )

        try:
            analysis = analyze_email(texto, topics)
        except LLMRefusal as exc:
            log.warning("Claude declino analizar %s: %s", gmail_id, exc)
            telegram.send(
                "No pude analizar un mail automaticamente.\n"
                f"Asunto: <b>{telegram.esc(email.subject)}</b>\n"
                "Revisalo a mano."
            )
            continue
        except Exception:
            # No lo marcamos como visto: queremos reintentarlo en la proxima
            # pasada, asi que tampoco movemos el cursor mas abajo.
            log.exception("Fallo el analisis de %s", gmail_id)
            stats["fallidos"] += 1
            continue

        with session_scope() as s:
            s.add(
                SeenMessage(
                    gmail_id=email.gmail_id,
                    thread_id=email.thread_id,
                    subject=email.subject,
                    sender=email.sender,
                    internal_date=email.date,
                    relevant=analysis.relevante,
                    topic_slug=analysis.tema or None,
                    summary=analysis.resumen,
                    analysis=analysis.model_dump(),
                )
            )

        if not analysis.relevante:
            continue

        stats["relevantes"] += 1
        stats["propuestas"] += _handle_relevant(email, analysis)

    # Si algo fallo, dejamos el cursor donde estaba. Mover el cursor con un
    # mail sin procesar lo perderia para siempre, porque la proxima consulta ya
    # no lo traeria. Los que si se procesaron no se repiten: los filtra
    # seen_messages.
    if stats["fallidos"]:
        log.warning(
            "%s mail(s) sin procesar; no muevo el cursor para reintentarlos.",
            stats["fallidos"],
        )
    else:
        with session_scope() as s:
            kv_set(s, "last_poll_epoch", str(new_cursor))

    log.info("Revision terminada: %s", stats)
    return stats


def _notify_suspicious(email: gmail_client.Email, veredicto) -> None:
    """Avisa de un mail que pide plata y no pasa la verificacion del remitente.

    No decimos que sea una estafa: decimos que no se pudo probar quien lo mando,
    que es justo lo que no se puede ignorar cuando hay plata de por medio.
    """
    telegram.send(
        "⚠️ <b>CUIDADO: mail de pago sin verificar</b>\n\n"
        f"<b>De:</b> {telegram.esc(email.sender)}\n"
        f"<b>Asunto:</b> {telegram.esc(email.subject)}\n\n"
        f"Este mail habla de dinero y <b>no pude verificar que venga de quien dice</b>.\n"
        f"<code>{telegram.esc(veredicto.resumen())}</code>\n\n"
        "<b>No uses los links ni los datos bancarios que traiga.</b> Si esperabas "
        "este pago, entra vos a la plataforma o confirmalo por otro medio con la "
        "persona de siempre.\n\n"
        f'<a href="{email.gmail_link()}">Verlo en Gmail</a>\n'
        "<i>No lo analice ni lo agende, justamente por esto.</i>"
    )


def _notify_private(email: gmail_client.Email) -> None:
    """Avisa que llego un mail de un remitente privado, sin analizarlo.

    Solo viaja a Telegram el asunto y el remitente, que ya los escribio el que
    lo mando. El cuerpo no sale de aca.
    """
    telegram.send(
        "\U0001f512 <b>Te llego un mail que no analizo</b>\n"
        f"<i>{telegram.esc(email.sender)}</i>\n\n"
        f"{telegram.esc(email.subject)}\n\n"
        f'<a href="{email.gmail_link()}">Abrirlo en Gmail</a>\n'
        "<i>Este remitente esta en tu lista privada, asi que el contenido no "
        "sale de tu maquina. Si hay una fecha limite, decimela y la agendo.</i>"
    )


def _handle_relevant(email: gmail_client.Email, analysis: EmailAnalysis) -> int:
    """Crea una propuesta por cada fecha limite, o avisa si no hay ninguna."""
    topic_name = analysis.tema or "mail"
    today = dt.datetime.now(settings.tz).date()
    created = 0

    for deadline in analysis.fechas_limite:
        when = parse_dt(deadline.fecha, deadline.hora)
        if when is None or when.date() < today:
            continue

        descripcion = (
            f"{analysis.resumen}\n\n"
            f"Accion: {analysis.accion or deadline.titulo}\n"
            f"Mail de: {email.sender}\n"
            f"Asunto: {email.subject}\n"
            f"https://mail.google.com/mail/u/0/#inbox/{email.thread_id}"
        )

        with session_scope() as s:
            proposal = Proposal(
                source="email",
                gmail_id=email.gmail_id,
                topic_slug=analysis.tema or None,
                title=deadline.titulo,
                description=descripcion,
                calendar_key="work_travel",
                start_at=when,
                all_day=not bool(deadline.hora),
                is_deadline=True,
                status="pending",
            )
            s.add(proposal)
            s.flush()
            proposal_id = proposal.id

        _notify_deadline(proposal_id, topic_name, email, analysis, deadline, when)
        created += 1

    if created == 0 and analysis.requiere_accion:
        telegram.send(
            f"\U0001f4ec <b>{telegram.esc(topic_name)}</b>\n"
            f"<i>{telegram.esc(email.sender)}</i>\n\n"
            f"{telegram.esc(analysis.resumen)}\n\n"
            f"⚠️ Tenes que hacer algo: {telegram.esc(analysis.accion)}\n"
            f"<i>No detecte una fecha limite. Si hay una, escribimela y la agendo.</i>"
        )

    return created


def _notify_deadline(
    proposal_id: int,
    topic_name: str,
    email: gmail_client.Email,
    analysis: EmailAnalysis,
    deadline,
    when: dt.datetime,
) -> None:
    dias = (when.astimezone(settings.tz).date() - dt.datetime.now(settings.tz).date()).days
    plazo = "es hoy" if dias == 0 else f"en {dias} dia{'s' if dias != 1 else ''}"

    lines = [
        f"\U0001f4cc <b>{telegram.esc(topic_name)}</b>",
        f"<i>{telegram.esc(email.sender)}</i>",
        "",
        telegram.esc(analysis.resumen),
        "",
        f"<b>Hay que: {telegram.esc(deadline.titulo)}</b>",
        f"\U0001f5d3 Vence {human_date(when, with_time=bool(deadline.hora))} ({plazo})",
    ]
    if deadline.cita:
        lines.append(f"\U0001f4ce <i>{telegram.esc(deadline.cita[:200])}</i>")
    if deadline.confianza != "alta":
        lines.append(
            f"⚠️ Confianza {deadline.confianza} en esa fecha, verificala."
        )
    lines.append("")
    lines.append("¿Lo agendo?")

    keyboard = telegram.buttons(
        [[("✅ Agendar", f"ok:{proposal_id}"), ("❌ Ignorar", f"no:{proposal_id}")]]
    )
    message_id = telegram.send("\n".join(lines), keyboard)

    if message_id:
        with session_scope() as s:
            proposal = s.get(Proposal, proposal_id)
            if proposal:
                proposal.telegram_message_id = message_id


# --------------------------------------------------------------------------- #
# Aprobar o descartar una propuesta
# --------------------------------------------------------------------------- #


def approve(proposal_id: int) -> str:
    # Reservamos la propuesta con un UPDATE condicional, que la base resuelve
    # de a uno. Si alguien ya la tomo, rowcount viene en cero y cortamos. Sin
    # esto, un doble toque del boton -o un reintento del webhook de Telegram
    # mientras el servicio despierta- crea el mismo evento dos veces.
    with session_scope() as s:
        if s.get(Proposal, proposal_id) is None:
            return "No encuentro esa propuesta."
        tomada = s.execute(
            update(Proposal)
            .where(Proposal.id == proposal_id, Proposal.status == "pending")
            .values(status="agendando")
        ).rowcount
    if not tomada:
        return "Ya estaba agendado."

    with session_scope() as s:
        proposal = s.get(Proposal, proposal_id)
        data = {
            "title": proposal.title,
            "description": proposal.description,
            "calendar_key": proposal.calendar_key,
            "start_at": proposal.start_at,
            "end_at": proposal.end_at,
            "all_day": proposal.all_day,
            "recurrence": proposal.recurrence,
            "is_deadline": proposal.is_deadline,
            "telegram_message_id": proposal.telegram_message_id,
        }

    try:
        event = calendar_client.create_event(
            calendar_key=data["calendar_key"],
            summary=data["title"],
            start=data["start_at"],
            end=data["end_at"],
            all_day=data["all_day"],
            description=data["description"],
            recurrence=data["recurrence"],
            is_deadline=data["is_deadline"],
        )
    except Exception:
        # Devolvemos la propuesta a pendiente para que puedas reintentarla.
        log.exception("No pude crear el evento de la propuesta %s", proposal_id)
        with session_scope() as s:
            fallida = s.get(Proposal, proposal_id)
            if fallida and fallida.status == "agendando":
                fallida.status = "pending"
        return "No pude crear el evento en el calendario. Mira los logs."

    with session_scope() as s:
        proposal = s.get(Proposal, proposal_id)
        if proposal:
            proposal.status = "approved"
            proposal.calendar_event_id = event.event_id
            proposal.calendar_id = event.calendar_id
            proposal.decided_at = dt.datetime.now(dt.timezone.utc)

    texto = (
        f"✅ Agendado: <b>{telegram.esc(data['title'])}</b>\n"
        f"{human_date(data['start_at'], with_time=not data['all_day'])}"
    )
    if data["telegram_message_id"]:
        telegram.edit(data["telegram_message_id"], texto)
        return "Agendado"
    telegram.send(texto)
    return "Agendado"


def reject(proposal_id: int) -> str:
    with session_scope() as s:
        proposal = s.get(Proposal, proposal_id)
        if proposal is None:
            return "No encuentro esa propuesta."
        proposal.status = "rejected"
        proposal.decided_at = dt.datetime.now(dt.timezone.utc)
        title = proposal.title
        message_id = proposal.telegram_message_id

    texto = f"❌ Descartado: <s>{telegram.esc(title)}</s>"
    if message_id:
        telegram.edit(message_id, texto)
    else:
        telegram.send(texto)
    return "Descartado"


def undo(proposal_id: int) -> str:
    """Borra del calendario un evento que el agente habia creado."""
    with session_scope() as s:
        proposal = s.get(Proposal, proposal_id)
        if proposal is None or proposal.status != "approved":
            return "No hay nada que deshacer."
        calendar_id, event_id = proposal.calendar_id, proposal.calendar_event_id
        title = proposal.title

    if calendar_id and event_id:
        calendar_client.delete_event(calendar_id, event_id)

    with session_scope() as s:
        proposal = s.get(Proposal, proposal_id)
        if proposal:
            proposal.status = "rejected"
            proposal.calendar_event_id = None

    return f"Saque del calendario: {title}"


# --------------------------------------------------------------------------- #
# Lo que le pedis por chat
# --------------------------------------------------------------------------- #


def proposal_from_chat(event: ChatEvent) -> int | None:
    """Convierte lo que escribiste en una propuesta con boton de confirmacion."""
    start = parse_dt(event.fecha_inicio, event.hora_inicio)
    if start is None:
        return None

    end = parse_dt(event.fecha_fin or event.fecha_inicio, event.hora_fin)
    if end is not None and end <= start and not event.todo_el_dia:
        end = start + dt.timedelta(hours=1)

    recurrence = None
    if event.dias_semana:
        until = None
        if event.repetir_hasta:
            try:
                until = dt.date.fromisoformat(event.repetir_hasta)
            except ValueError:
                until = None
        recurrence = build_rrule(event.dias_semana, until)

    with session_scope() as s:
        proposal = Proposal(
            source="chat",
            title=event.titulo,
            description=event.notas,
            calendar_key=event.calendario,
            start_at=start,
            end_at=end,
            all_day=event.todo_el_dia,
            recurrence=recurrence,
            is_deadline=event.es_fecha_limite,
            status="pending",
        )
        s.add(proposal)
        s.flush()
        proposal_id = proposal.id

    cuando = human_date(start, with_time=not event.todo_el_dia)
    lines = [f"\U0001f4dd <b>{telegram.esc(event.titulo)}</b>", f"\U0001f5d3 {cuando}"]
    if event.fecha_fin and event.fecha_fin != event.fecha_inicio and end:
        lines.append(f"hasta {human_date(end, with_time=not event.todo_el_dia)}")
    if event.dias_semana:
        nombres = {
            "MO": "lun", "TU": "mar", "WE": "mie", "TH": "jue",
            "FR": "vie", "SA": "sab", "SU": "dom",
        }
        repite = ", ".join(nombres[d] for d in event.dias_semana)
        extra = f" hasta el {event.repetir_hasta}" if event.repetir_hasta else ""
        lines.append(f"\U0001f501 Todas las semanas: {repite}{extra}")
    calendario = calendar_client.CALENDARS.get(event.calendario)
    if calendario:
        lines.append(f"\U0001f4c5 {calendario[0]}")
    lines.append("")
    lines.append("¿Lo agrego?")

    keyboard = telegram.buttons(
        [[("✅ Agregar", f"ok:{proposal_id}"), ("❌ Cancelar", f"no:{proposal_id}")]]
    )
    message_id = telegram.send("\n".join(lines), keyboard)

    if message_id:
        with session_scope() as s:
            proposal = s.get(Proposal, proposal_id)
            if proposal:
                proposal.telegram_message_id = message_id

    return proposal_id


# --------------------------------------------------------------------------- #
# Recordatorios y resumenes
# --------------------------------------------------------------------------- #


def send_reminders() -> int:
    """Avisa de los vencimientos que se acercan.

    Es idempotente: cada aviso queda anotado en reminders_sent, asi que se
    puede llamar muchas veces por dia sin repetir nada. Eso es a proposito, y
    es lo que hace que los recordatorios sobrevivan a un hosting que duerme:
    no dependemos de que el proceso este vivo a una hora exacta, sino de que
    en algun momento del dia corra una revision.
    """
    ahora = dt.datetime.now(settings.tz)
    if ahora.hour < settings.daily_digest_hour:
        return 0  # todavia es temprano, no te despertamos

    today = ahora.date()
    sent = 0

    with session_scope() as s:
        pendientes = list(
            s.scalars(
                select(Proposal).where(
                    Proposal.status == "approved",
                    Proposal.is_deadline.is_(True),
                )
            )
        )

        for proposal in pendientes:
            dias = (proposal.start_at.astimezone(settings.tz).date() - today).days
            if dias < 0 or dias not in REMINDER_OFFSETS:
                continue
            ya = list(proposal.reminders_sent or [])
            if dias in ya:
                continue

            if dias == 0:
                encabezado = "\U0001f6a8 <b>Vence HOY</b>"
            elif dias == 1:
                encabezado = "⏰ <b>Vence manana</b>"
            else:
                encabezado = f"⏰ <b>Faltan {dias} dias</b>"

            telegram.send(
                f"{encabezado}\n\n"
                f"{telegram.esc(proposal.title)}\n"
                f"\U0001f5d3 {human_date(proposal.start_at, with_time=not proposal.all_day)}",
                telegram.buttons([[("✔️ Ya lo hice", f"done:{proposal.id}")]]),
            )
            proposal.reminders_sent = ya + [dias]
            sent += 1

    return sent


def mark_done(proposal_id: int) -> str:
    with session_scope() as s:
        proposal = s.get(Proposal, proposal_id)
        if proposal is None:
            return "No encuentro eso."
        proposal.status = "done"
        return f"Listo: {proposal.title}"


def format_agenda(days: int = 7) -> str:
    try:
        eventos = calendar_client.upcoming(days)
    except Exception:
        log.exception("No pude leer el calendario")
        return "No pude leer el calendario. Mira los logs."

    if not eventos:
        return f"No tenes nada agendado en los proximos {days} dias."

    lines = [f"\U0001f4c5 <b>Proximos {days} dias</b>", ""]
    for event in eventos[:30]:
        cuando = event.start[:16].replace("T", " ")
        lines.append(f"• <b>{cuando}</b> {telegram.esc(event.summary)}")
    return "\n".join(lines)


def format_pending() -> str:
    today = dt.datetime.now(settings.tz).date()
    with session_scope() as s:
        proposals = list(
            s.scalars(
                select(Proposal)
                .where(Proposal.status.in_(["approved", "pending"]))
                .order_by(Proposal.start_at)
            )
        )
        rows = [
            (p.status, p.title, p.start_at)
            for p in proposals
            if p.is_deadline and p.start_at.astimezone(settings.tz).date() >= today
        ]

    if not rows:
        return "No tenes vencimientos pendientes."

    lines = ["\U0001f4cb <b>Pendientes</b>", ""]
    for status, title, start_at in rows[:30]:
        dias = (start_at.astimezone(settings.tz).date() - today).days
        plazo = "hoy" if dias == 0 else f"en {dias}d"
        marca = "⏳" if status == "pending" else "✅"
        lines.append(f"{marca} {telegram.esc(title)} - <b>{plazo}</b>")
    if any(s == "pending" for s, _, _ in rows):
        lines.append("")
        lines.append("⏳ = esperando que me confirmes")
    return "\n".join(lines)

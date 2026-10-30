"""Analytics and report tables. Reports are plain rows, so CSV and PDF stay identical."""
import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import csv_safe
from app.models import Festival
from app.services.finance import department_lines


@dataclass
class Filters:
    day: date | None = None
    event: str | None = None
    department: str | None = None
    venue: str | None = None
    status: str | None = None


def rows(db: Session, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(text(sql), params).mappings()]


EVENT_ROWS = """
    SELECT e.code, e.name, e.status, v.name AS venue, v.capacity,
           to_char(e.start_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS starts,
           (SELECT count(*) FROM registrations r WHERE r.event_id=e.id) AS registered,
           (SELECT count(*) FROM registrations r WHERE r.event_id=e.id AND r.attendance='present') AS attended,
           (SELECT count(*) FROM volunteer_shifts s WHERE s.event_id=e.id AND s.status<>'cancelled') AS staffed,
           e.volunteers_needed AS needed
    FROM events e LEFT JOIN venues v ON v.id=e.venue_id
    WHERE e.festival_id=:f AND e.deleted_at IS NULL
      AND (CAST(:day AS date) IS NULL OR (e.start_at AT TIME ZONE 'Asia/Kolkata')::date = CAST(:day AS date))
      AND (CAST(:event AS text) IS NULL OR e.code = :event)
      AND (CAST(:venue AS text) IS NULL OR v.code = :venue)
      AND (CAST(:status AS text) IS NULL OR e.status = :status)
    ORDER BY e.start_at NULLS LAST, e.code"""


def event_rows(db: Session, f: int, x: Filters) -> list[dict[str, Any]]:
    return rows(db, EVENT_ROWS, f=f, day=x.day, event=x.event, venue=x.venue, status=x.status)


def accommodation_rows(db: Session, f: int, x: Filters) -> list[dict[str, Any]]:
    return rows(db, """
        SELECT h.name AS hostel, h.kind, h.status,
               coalesce(sum(r.capacity) FILTER (WHERE h.status='available' AND r.status='available'), 0) AS beds, count(a.id) AS occupied,
               h.cost_per_bed_night AS rate
        FROM hostels h JOIN rooms r ON r.hostel_id=h.id
        LEFT JOIN accommodation_allocations a ON a.room_id=r.id AND a.status<>'cancelled'
        WHERE h.festival_id=:f GROUP BY h.id ORDER BY h.kind DESC, h.code""", f=f)


def volunteer_rows(db: Session, f: int, x: Filters) -> list[dict[str, Any]]:
    return rows(db, """
        SELECT d.name AS department, count(DISTINCT v.id) AS volunteers,
               count(s.id) AS shifts,
               count(s.id) FILTER (WHERE s.attendance='present') AS attended,
               count(s.id) FILTER (WHERE s.attendance='absent') AS absent,
               (SELECT count(*) FROM tasks t WHERE t.department_id=d.id) AS tasks,
               (SELECT count(*) FROM tasks t WHERE t.department_id=d.id AND t.status='done') AS tasks_done
        FROM departments d LEFT JOIN volunteers v ON v.department_id=d.id AND v.status='active'
        LEFT JOIN volunteer_shifts s ON s.volunteer_id=v.id AND s.status<>'cancelled'
        WHERE d.festival_id=:f AND (CAST(:department AS text) IS NULL OR d.name=:department)
        GROUP BY d.id ORDER BY d.name""", f=f, department=x.department)


def transport_rows(db: Session, f: int, x: Filters) -> list[dict[str, Any]]:
    return rows(db, """
        SELECT r.code, r.name, r.status, v.code AS vehicle, v.capacity AS seats,
               (SELECT count(*) FROM transport_assignments t WHERE t.route_id=r.id) AS passengers,
               r.delay_minutes,
               to_char(r.expected_arrival_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS arrives
        FROM routes r LEFT JOIN vehicles v ON v.id=r.vehicle_id
        WHERE r.festival_id=:f AND (CAST(:status AS text) IS NULL OR r.status=:status)
          AND (CAST(:day AS date) IS NULL OR (r.departure_at AT TIME ZONE 'Asia/Kolkata')::date = CAST(:day AS date))
        ORDER BY r.departure_at""", f=f, status=x.status, day=x.day)


def finance_rows(db: Session, f: int, x: Filters) -> list[dict[str, Any]]:
    keep = ("department", "approved", "committed", "actual", "pending", "remaining")
    return [{k: line[k] for k in keep} for line in department_lines(db, f) if not x.department or line["department"] == x.department]


def incident_rows(db: Session, f: int, x: Filters) -> list[dict[str, Any]]:
    return rows(db, """
        SELECT code, category, location, priority, status, assigned_team,
               to_char(reported_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS reported
        FROM incidents WHERE festival_id=:f AND (CAST(:status AS text) IS NULL OR status=:status)
          AND (CAST(:day AS date) IS NULL OR (reported_at AT TIME ZONE 'Asia/Kolkata')::date = CAST(:day AS date))
        ORDER BY reported_at DESC""", f=f, status=x.status, day=x.day)


def simulation_rows(db: Session, f: int, x: Filters) -> list[dict[str, Any]]:
    return rows(db, """
        SELECT s.id, s.report->>'title' AS change, s.status, u.name AS run_by,
               (s.report->'summary'->>'critical')::int AS critical,
               to_char(s.created_at AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI') AS run_at
        FROM simulations s JOIN users u ON u.id=s.created_by_id
        WHERE s.festival_id=:f AND (CAST(:status AS text) IS NULL OR s.status=:status) ORDER BY s.id DESC""", f=f, status=x.status)


@dataclass(frozen=True)
class Report:
    title: str
    permission: str
    columns: tuple[str, ...]
    load: Callable[[Session, int, Filters], list[dict[str, Any]]]
    simulated: bool = False


REPORTS = {
    "events": Report("Event performance", "events.read", ("code", "name", "status", "venue", "capacity", "starts", "registered", "attended", "staffed", "needed"), event_rows),
    "accommodation": Report("Accommodation utilization", "accommodation.read", ("hostel", "kind", "status", "beds", "occupied", "rate"), accommodation_rows),
    "volunteers": Report("Department and volunteer performance", "volunteers.read", ("department", "volunteers", "shifts", "attended", "absent", "tasks", "tasks_done"), volunteer_rows, True),
    "transport": Report("Transportation performance", "transport.read", ("code", "name", "status", "vehicle", "seats", "passengers", "delay_minutes", "arrives"), transport_rows, True),
    "finance": Report("Financial report", "finance.read", ("department", "approved", "committed", "actual", "pending", "remaining"), finance_rows, True),
    "incidents": Report("Incident statistics", "incidents.read", ("code", "category", "location", "priority", "status", "assigned_team", "reported"), incident_rows, True),
    "simulations": Report("Simulation history", "simulation.run", ("id", "change", "status", "run_by", "critical", "run_at"), simulation_rows),
}


def filter_note(x: Filters) -> str:
    parts = [f"{k}: {v}" for k, v in vars(x).items() if v]
    return "Filters: " + ", ".join(parts) if parts else "All records"


def to_csv(report: Report, data: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(report.columns)
    for row in data:
        writer.writerow([csv_safe(row.get(c)) for c in report.columns])
    return out.getvalue()


def pdf_cell(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}" if isinstance(value, Decimal) else str(value)


def to_pdf(report: Report, festival: Festival, data: list[dict[str, Any]], filters: Filters) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    styles = getSampleStyleSheet()
    cell = styles["BodyText"].clone("cell", fontSize=8, leading=10)
    out = io.BytesIO()
    document = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=report.title)
    story: list[Any] = [Paragraph(escape(f"{festival.name}: {report.title}"), styles["Title"]), Paragraph(escape(filter_note(filters)), styles["Normal"])]
    if report.simulated:
        story.append(Paragraph("Contains simulated demo data.", styles["Italic"]))
    story.append(Spacer(1, 6 * mm))
    if data:
        header = [Paragraph(f"<b>{escape(c.replace('_', ' ').title())}</b>", cell) for c in report.columns]
        table = LongTable([header, *[[Paragraph(escape(pdf_cell(r.get(c))), cell) for c in report.columns] for r in data]], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7ECF7")), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B7C0D4")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(table)
    else:
        story.append(Paragraph("No records match these filters.", styles["Normal"]))
    document.build(story)
    return out.getvalue()


def overview(db: Session, f: int, x: Filters, permissions: set[str]) -> dict[str, Any]:
    """Each section appears only for roles allowed to read that module."""
    result: dict[str, Any] = {}
    if "events.read" in permissions:
        events = event_rows(db, f, x)
        result["events"] = events
        result["registration"] = {
            "registrations": sum(e["registered"] for e in events), "events": len(events), "over_capacity": sum(1 for e in events if e["capacity"] is not None and e["registered"] > e["capacity"]),
            "attended": sum(e["attended"] for e in events),
        }
    if "participants.read" in permissions:
        result["colleges"] = rows(db, """SELECT p.college, count(DISTINCT p.id) AS participants, count(r.id) AS registrations FROM participants p
            LEFT JOIN registrations r ON r.participant_id=p.id WHERE p.festival_id=:f AND p.deleted_at IS NULL GROUP BY p.college ORDER BY participants DESC LIMIT 10""", f=f)
        result["attendance"] = rows(db, "SELECT checkin_status AS status, count(*) AS count FROM participants WHERE festival_id=:f AND deleted_at IS NULL GROUP BY 1 ORDER BY 2 DESC", f=f)
    if "accommodation.read" in permissions:
        result["accommodation"] = accommodation_rows(db, f, x)
    if "volunteers.read" in permissions:
        result["departments"] = volunteer_rows(db, f, x)
    if "transport.read" in permissions:
        trips = transport_rows(db, f, x)
        result["transport"] = {"trips": len(trips), "delayed": sum(t["status"] == "delayed" for t in trips), "passengers": sum(t["passengers"] for t in trips),
                               "seats": sum(t["seats"] or 0 for t in trips), "delay_minutes": sum(t["delay_minutes"] for t in trips)}
    if "finance.read" in permissions:
        result["finance"] = finance_rows(db, f, x)
    if "incidents.read" in permissions:
        result["incidents"] = rows(db, "SELECT status, count(*) AS count FROM incidents WHERE festival_id=:f GROUP BY 1 ORDER BY 2 DESC", f=f)
        result["incident_priorities"] = rows(db, "SELECT priority, count(*) AS count FROM incidents WHERE festival_id=:f GROUP BY 1 ORDER BY 2 DESC", f=f)
    if "simulation.run" in permissions:
        result["simulations"] = rows(db, "SELECT status, count(*) AS count FROM simulations WHERE festival_id=:f GROUP BY 1 ORDER BY 2 DESC", f=f)
    return result

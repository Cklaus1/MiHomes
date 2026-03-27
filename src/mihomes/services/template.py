"""Template service — reusable checklists that instantiate into tasks."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mihomes.models.template import Template, TemplateItem
from mihomes.models.task import Task, TaskPriority, RecurrenceFrequency
from mihomes.services.audit import record_change, snapshot_instance
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier
from mihomes.services.task import create_task


def create_template(
    session: Session,
    name: str,
    *,
    description: str | None = None,
    steps: list[str] | None = None,
    slug: str | None = None,
) -> Template:
    slug = ensure_unique_slug(session, Template, slug or generate_slug(name))
    template = Template(name=name, slug=slug, description=description)
    session.add(template)
    session.flush()

    if steps:
        for i, step_title in enumerate(steps):
            item = TemplateItem(template_id=template.id, title=step_title.strip(), order=i)
            session.add(item)
        session.flush()

    record_change(session, "template", template.id, "create", snapshot_instance(template))
    return template


def list_templates(session: Session) -> list[Template]:
    return session.query(Template).order_by(Template.name).all()


def get_template(session: Session, id_or_slug: str) -> Template:
    return resolve_identifier(session, Template, id_or_slug)


def run_template(
    session: Session,
    template_id_or_slug: str,
    property_id_or_slug: str,
    *,
    due_date: date | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
) -> list[Task]:
    """Instantiate a template into tasks for a property."""
    template = resolve_identifier(session, Template, template_id_or_slug)
    tasks = []
    base_due = due_date or date.today()

    for i, item in enumerate(template.items):
        task_due = base_due + timedelta(days=i)  # stagger by 1 day per step
        task = create_task(
            session, item.title, property_id_or_slug,
            priority=priority, due_date=task_due,
            description=f"From template: {template.name}",
        )
        tasks.append(task)

    return tasks


def delete_template(session: Session, id_or_slug: str) -> str:
    template = resolve_identifier(session, Template, id_or_slug)
    name = template.name
    record_change(session, "template", template.id, "delete", snapshot_instance(template))
    session.delete(template)
    session.flush()
    return name

"""Document CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_enum, format_error, format_panel, format_success
from mihomes.db import get_session
from mihomes.models.document import DocumentType
from mihomes.services import document as doc_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="document", help="Manage documents and files")


@app.command("add")
def add_document(
    title: str = typer.Argument(..., help="Document title"),
    file_path: str = typer.Option(..., "--file", "-f", help="Path to the file"),
    doc_type: DocumentType = typer.Option(..., "--type", "-t", help="Document type"),
    to: Optional[str] = typer.Option(None, "--to", help="Entity reference e.g. property:my-house, asset:3"),
    expires: Optional[str] = typer.Option(None, "--expires", help="Expiry date YYYY-MM-DD"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Add a new document."""
    from datetime import date
    entity_type = None
    entity_id = None
    if to:
        parts = to.split(":")
        if len(parts) == 2:
            entity_type = parts[0]
            try:
                entity_id = int(parts[1])
            except ValueError:
                format_error(f"Invalid entity reference: {to}. Entity ID must be numeric (e.g. property:1)")
                raise typer.Exit(1)
        else:
            format_error(f"Invalid entity reference: {to}. Expected format: type:id")
            raise typer.Exit(1)
    with get_session() as session:
        try:
            doc = doc_svc.create_document(
                session, title, file_path, doc_type,
                entity_type=entity_type, entity_id=entity_id,
                expires_at=date.fromisoformat(expires) if expires else None,
                notes=notes,
            )
            format_success(f"Document '{doc.title}' added (slug: {doc.slug}, type: {doc.document_type.value})")
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_documents(
    doc_type: Optional[DocumentType] = typer.Option(None, "--type", "-t"),
    expiring: Optional[int] = typer.Option(None, "--expiring", help="Show documents expiring in N days"),
    entity: Optional[str] = typer.Option(None, "--entity", help="Filter by entity e.g. property:1"),
):
    """List documents."""
    with get_session() as session:
        if expiring is not None:
            docs = doc_svc.list_expiring(session, days=expiring)
        else:
            entity_type = None
            entity_id = None
            if entity:
                parts = entity.split(":")
                if len(parts) == 2:
                    entity_type = parts[0]
                    try:
                        entity_id = int(parts[1])
                    except ValueError:
                        pass
            docs = doc_svc.list_documents(
                session, document_type=doc_type,
                entity_type=entity_type, entity_id=entity_id,
            )

        if not docs:
            console.print("[dim]No documents found.[/dim]")
            return

        table = Table(title="Documents")
        table.add_column("ID", style="dim")
        table.add_column("Title", style="bold")
        table.add_column("Type")
        table.add_column("File")
        table.add_column("Entity")
        table.add_column("Expires")
        table.add_column("Slug", style="dim")
        for d in docs:
            entity_str = f"{d.entity_type}:{d.entity_id}" if d.entity_type else "-"
            table.add_row(
                str(d.id), d.title, format_enum(d.document_type),
                d.file_path, entity_str,
                str(d.expires_at) if d.expires_at else "-",
                d.slug,
            )
        console.print(table)


@app.command("show")
def show_document(id_or_slug: str = typer.Argument(...)):
    """Show document details."""
    with get_session() as session:
        try:
            doc = doc_svc.get_document(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        content = {
            "ID": str(doc.id),
            "Slug": doc.slug,
            "Type": format_enum(doc.document_type),
            "File Path": doc.file_path,
            "Entity": f"{doc.entity_type}:{doc.entity_id}" if doc.entity_type else None,
            "Expires": str(doc.expires_at) if doc.expires_at else None,
            "Notes": doc.notes,
        }
        console.print(format_panel(f"Document: {doc.title}", content))


@app.command("edit")
def edit_document(
    id_or_slug: str = typer.Argument(...),
    title: Optional[str] = typer.Option(None, "--title"),
    expires: Optional[str] = typer.Option(None, "--expires", help="Expiry date YYYY-MM-DD"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Edit a document."""
    from datetime import date
    kwargs = {}
    if title is not None: kwargs["title"] = title
    if expires is not None: kwargs["expires_at"] = date.fromisoformat(expires)
    if notes is not None: kwargs["notes"] = notes
    if not kwargs:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        try:
            doc = doc_svc.update_document(session, id_or_slug, **kwargs)
            format_success(f"Document '{doc.title}' updated")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("delete")
def delete_document(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete a document."""
    with get_session() as session:
        try:
            doc = doc_svc.get_document(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not force:
            typer.confirm(f"Delete document '{doc.title}'?", abort=True)
        name = doc_svc.delete_document(session, id_or_slug)
        format_success(f"Document '{name}' deleted")

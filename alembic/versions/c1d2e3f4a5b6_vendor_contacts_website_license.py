"""vendor contacts, website, license_number

Revision ID: c1d2e3f4a5b6
Revises: b3c4d5e6f7a8
Create Date: 2026-05-21

"""
import json

from alembic import op
import sqlalchemy as sa

revision = 'c1d2e3f4a5b6'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('vendors', sa.Column('contacts', sa.JSON, nullable=True))
    op.add_column('vendors', sa.Column('website', sa.String(500), nullable=True))
    op.add_column('vendors', sa.Column('license_number', sa.String(100), nullable=True))

    # Migrate existing phone/email/contact_name into contacts[0]
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, contact_name, phone, email FROM vendors")
    ).fetchall()
    for row in rows:
        if row.contact_name or row.phone or row.email:
            contact = {
                "name": row.contact_name or "",
                "role": "",
                "phone": row.phone or "",
                "email": row.email or "",
            }
            connection.execute(
                sa.text("UPDATE vendors SET contacts = :c WHERE id = :id"),
                {"c": json.dumps([contact]), "id": row.id},
            )


def downgrade() -> None:
    op.drop_column('vendors', 'license_number')
    op.drop_column('vendors', 'website')
    op.drop_column('vendors', 'contacts')

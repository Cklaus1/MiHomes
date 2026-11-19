"""money columns to integer cents (M1/R5.2)

Convert every money column from Float dollars to Integer cents so that finance
math is exact. Paired with the `Money` TypeDecorator (src/mihomes/type/money.py)
which presents these columns as float dollars to the application while storing
exact integer cents on disk.

Revision ID: b3f5c1d9a72e
Revises: ce1a992f291e
Create Date: 2026-07-29 17:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3f5c1d9a72e'
down_revision: Union[str, None] = 'ce1a992f291e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, nullable) for every money column the Money type now backs.
MONEY_COLUMNS = [
    ("budgets", "amount", False),
    ("transactions", "amount", False),
    ("recurring_expenses", "amount", False),
    ("work_orders", "estimated_cost", True),
    ("work_orders", "actual_cost", True),
    ("assets", "purchase_price", True),
    ("assets", "replacement_cost_estimate", True),
    ("asset_price_entries", "price", False),
    ("insurance_policies", "coverage_limit", True),
    ("insurance_policies", "deductible", True),
    ("insurance_policies", "annual_premium", True),
    ("events", "budget", True),
    ("consumables", "unit_price", True),
    ("consumable_price_entries", "price", False),
    ("contracts", "cost", True),
]


def upgrade() -> None:
    # Phase 1: scale dollars -> integer cents IN PLACE, while the column is still
    # Float. ROUND(x*100) rounds half-away-from-zero, matching the Money type's
    # ROUND_HALF_UP, so 19.99 -> 1999 exactly (19.99*100 is 1998.9999.. in binary).
    # Doing this BEFORE the type change is essential: flipping to Integer first
    # would truncate 19.99 -> 19 during the batch table-copy.
    for table, col, _nullable in MONEY_COLUMNS:
        op.execute(
            f"UPDATE {table} SET {col} = CAST(ROUND({col} * 100) AS INTEGER) "
            f"WHERE {col} IS NOT NULL"
        )

    # Phase 2: change the declared type to Integer. The values are already whole
    # cents, so the batch copy is loss-free.
    for table, col, nullable in MONEY_COLUMNS:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                col,
                existing_type=sa.Float(),
                type_=sa.Integer(),
                existing_nullable=nullable,
            )


def downgrade() -> None:
    # Reverse order: widen the type back to Float FIRST so the column can hold
    # fractional dollars, THEN divide cents -> dollars. Dividing while the column
    # is still Integer affinity would truncate 1999/100 -> 19.
    for table, col, nullable in MONEY_COLUMNS:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                col,
                existing_type=sa.Integer(),
                type_=sa.Float(),
                existing_nullable=nullable,
            )

    for table, col, _nullable in MONEY_COLUMNS:
        op.execute(
            f"UPDATE {table} SET {col} = {col} / 100.0 WHERE {col} IS NOT NULL"
        )

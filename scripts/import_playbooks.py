"""Import markdown playbooks into ONE account's playbooks (2026-10-21).

Playbooks moved from shared files (`knowledge/playbooks/*.md`, visible to every account) to each
account's own rows. This moves an account's existing files in, once:

    DATABASE_URL=... python scripts/import_playbooks.py --account belle --dir knowledge/playbooks

- **Idempotent**: a playbook whose slug already exists in the account is skipped, so re-running
  after a partial failure (or by accident) adds nothing twice.
- **One account only**, named by its slug; it refuses if the slug does not exist.
- Binds the tenant context the same way the app does (`account_context` for the row stamp,
  `bind_account_guc` for the RLS policy) — without the GUC, FORCE RLS refuses every insert.
- `--dry-run` reports what would be imported and writes nothing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--account", required=True, help="the account's slug, e.g. belle")
    parser.add_argument("--dir", required=True, type=Path, help="folder of .md playbooks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set — refusing to guess which database to write.", file=sys.stderr)
        return 2
    files = sorted(args.dir.glob("*.md"))
    if not files:
        print(f"No .md files in {args.dir}", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from mihomes.services import playbook as pb_svc
    from mihomes.tenancy import account_context
    from mihomes.tenancy.connection import bind_account_guc

    engine = create_engine(url, future=True)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        row = s.execute(
            text("SELECT id, name FROM accounts WHERE slug = :slug"), {"slug": args.account}
        ).one_or_none()
        if row is None:
            print(f"No account with slug {args.account!r}.", file=sys.stderr)
            return 1
        account_id, account_name = row
        print(f"Account: {account_name} ({args.account})")

        created = skipped = 0
        with account_context(account_id):
            bind_account_guc(s, account_id)
            for f in files:
                slug = f.stem
                if pb_svc.get_playbook(s, slug) is not None:
                    print(f"  skip   {slug} (already there)")
                    skipped += 1
                    continue
                if args.dry_run:
                    print(f"  would  {slug}")
                    created += 1
                    continue
                pb, _ = pb_svc.import_markdown(s, slug, f.read_text(encoding="utf-8"))
                print(f"  import {slug} — {pb.name}, {len(pb_svc.checklist(pb))} steps")
                created += 1
            if args.dry_run:
                s.rollback()
            else:
                s.commit()
    verb = "would import" if args.dry_run else "imported"
    print(f"{verb} {created}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

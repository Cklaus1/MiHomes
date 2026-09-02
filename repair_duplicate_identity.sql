-- Repair the forked identity for mlim@fusen.world in mihomes_dev.
--
-- Two rows exist for one address, created by the signup bug fixed in 8a4cc36:
--
--   4e0cbf1e-2a68-4e31-90cd-f5c1c0d1d27a  google_sub set, no password   owner of Belle Estate
--   01a0627a-0115-72c3-bb0e-ec808eb583cc  password only, no google_sub  owns nothing
--
-- The orphan has ZERO references across all seven FK columns that point at `users`
-- (document_access.granted_by, invites.created_by, memberships.invited_by,
--  memberships.user_id, password_reset_tokens.user_id, sessions.user_id, staff.user_id) —
-- verified before writing this. So deleting it strands nothing.
--
-- Goal: one identity that can sign in with EITHER Google or the password set on 2026-09-02,
-- keeping the Belle Estate ownership that lives on the Google row.
--
-- **Statement order is load-bearing.** The obvious version — UPDATE the keeper, then DELETE
-- the orphan — fails:
--
--   ERROR: duplicate key value violates unique constraint "uq_users_email_password"
--
-- That index is `UNIQUE (lower(email)) WHERE password_hash IS NOT NULL`. After the UPDATE and
-- before the DELETE, both rows carry a password on the same address, and the index is checked
-- per statement rather than at COMMIT. So the credential is stashed, the orphan deleted to
-- vacate the index, and only then written onto the keeper.
--
-- Backup taken first: mihomes_dev_backup_20260902_152829.sql (pg_dump, 181K).
-- Runs in one transaction with post-condition checks that RAISE rather than commit a
-- half-repair.

BEGIN;

CREATE TEMP TABLE _carry ON COMMIT DROP AS
  SELECT password_hash, password_set_at
    FROM users
   WHERE id = '01a0627a-0115-72c3-bb0e-ec808eb583cc';

DELETE FROM users
 WHERE id = '01a0627a-0115-72c3-bb0e-ec808eb583cc';

UPDATE users
   SET password_hash   = (SELECT password_hash   FROM _carry),
       password_set_at = (SELECT password_set_at FROM _carry)
 WHERE id = '4e0cbf1e-2a68-4e31-90cd-f5c1c0d1d27a';

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM users WHERE lower(email) = 'mlim@fusen.world';
  IF n <> 1 THEN
    RAISE EXCEPTION 'expected exactly 1 row for the address, found %', n;
  END IF;

  SELECT count(*) INTO n FROM users
   WHERE id = '4e0cbf1e-2a68-4e31-90cd-f5c1c0d1d27a'
     AND google_sub IS NOT NULL
     AND password_hash IS NOT NULL;
  IF n <> 1 THEN
    RAISE EXCEPTION 'the surviving row does not carry both identities';
  END IF;

  SELECT count(*) INTO n FROM memberships
   WHERE user_id = '4e0cbf1e-2a68-4e31-90cd-f5c1c0d1d27a' AND status = 'active';
  IF n <> 1 THEN
    RAISE EXCEPTION 'the surviving row lost its Belle Estate membership';
  END IF;
END $$;

COMMIT;

SELECT u.email,
       u.google_sub   IS NOT NULL AS google_signin,
       u.password_hash IS NOT NULL AS password_signin,
       m.role,
       a.name AS account
  FROM users u
  LEFT JOIN memberships m ON m.user_id = u.id
  LEFT JOIN accounts    a ON a.id      = m.account_id
 WHERE lower(u.email) = 'mlim@fusen.world';

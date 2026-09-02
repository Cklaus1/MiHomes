-- Remove the throwaway users this diagnostic session created in mihomes_dev.
--
-- These were minted by reproducing the signup 403 against the live dev database. Every one is
-- a `*.test.local` address created on 2026-09-02, holds no membership, and owns nothing.
--
-- **Scoped by an explicit address list, not by "accountless".** Two real accounts —
-- millena.lim101@gmail.com and tester@test.com — are also accountless, and they predate this
-- session (created 2026-09-02 10:24/10:25, before the first repro at 14:18). Deleting by the
-- accountless predicate would take them too. They are left alone: with the 403 handler in
-- 8a4cc36 they now land on /onboarding/ instead of a JSON wall, which is the intended
-- experience for a user who has not finished onboarding.
--
-- Guarded so it cannot widen: the DELETE requires no membership and the .test.local suffix,
-- and the post-condition refuses to commit if the row count is not what was surveyed.

BEGIN;

DELETE FROM sessions
 WHERE user_id IN (
   SELECT u.id FROM users u
    LEFT JOIN memberships m ON m.user_id = u.id
    WHERE m.id IS NULL
      AND u.email LIKE '%.test.local'
 );

DELETE FROM users u
 WHERE u.email LIKE '%.test.local'
   AND NOT EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = u.id);

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM users WHERE email LIKE '%.test.local';
  IF n <> 0 THEN
    RAISE EXCEPTION 'diagnostic users remain: %', n;
  END IF;

  -- The two pre-existing real accounts must survive untouched.
  SELECT count(*) INTO n FROM users
   WHERE lower(email) IN ('millena.lim101@gmail.com', 'tester@test.com');
  IF n <> 2 THEN
    RAISE EXCEPTION 'a pre-existing account was deleted — expected 2, found %', n;
  END IF;

  SELECT count(*) INTO n FROM users WHERE lower(email) = 'mlim@fusen.world';
  IF n <> 1 THEN
    RAISE EXCEPTION 'the repaired identity was disturbed — found % rows', n;
  END IF;
END $$;

COMMIT;

SELECT u.email,
       u.google_sub    IS NOT NULL AS google_signin,
       u.password_hash IS NOT NULL AS password_signin,
       m.role,
       a.name AS account
  FROM users u
  LEFT JOIN memberships m ON m.user_id = u.id
  LEFT JOIN accounts    a ON a.id      = m.account_id
 ORDER BY u.email;

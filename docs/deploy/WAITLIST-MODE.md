# Waitlist mode — invite-only signup

One switch in `~/.mihomes/run/web.env`:

```
MIHOMES_SIGNUP_MODE=waitlist
```

With it set:

- The homepage shows the waitlist form ("Join the waitlist") instead of "Start free".
- `/signup` sends strangers to the waitlist. A first Google sign-in by someone with no account is
  refused with "MiHomes is invite-only for now…".
- **Everyone who already has an account signs in as before**, with a password or Google.
- Tester invite links and team invites (Settings → Members) still let people create an account.

Remove the line and restart to reopen signup. Nothing else changes.

## One-time setup on a server

The `waitlist` table was only ever created for the old standalone landing site, so the main
database does not have it yet (checked on the Droplet 2026-11-13). Create it with the landing
migration tree, which owns that one table and keeps its own version table:

```bash
cd ~/MiHomes
mapfile -t E < <(grep -v '^\s*$' ~/.mihomes/run/web.env)
env -i "${E[@]}" .venv/bin/alembic -n landing upgrade head    # creates `waitlist`
echo 'MIHOMES_SIGNUP_MODE=waitlist' >> ~/.mihomes/run/web.env
sudo -n ~/.mihomes/run/boot.sh                                 # restart with the new env
```

## Day to day

```bash
mapfile -t E < <(grep -v '^\s*$' ~/.mihomes/run/web.env)
env -i "${E[@]}" ~/MiHomes/.venv/bin/mihomes waitlist list                      # who signed up
env -i "${E[@]}" ~/MiHomes/.venv/bin/mihomes waitlist invite friend@example.com # tester link
```

`invite` prints a link that works for **that email address only**, for 14 days (`--days N` to
change). Email is not live yet, so send the link yourself. The tester can then sign up with a
password, or with "Sign up with Google" as long as their Google address is the invited one.
The link uses `MIHOMES_BASE_URL`, falling back to https://mihomes.ai (`--base-url` overrides).

No confirmation email is sent to people who join the waitlist. Their rows are kept: the nightly
cleanup only deletes unconfirmed rows that were actually *sent* a confirmation.

## The site passcode

`MIHOMES_SITE_PASSCODE` still puts the passcode screen in front of `/`, `/login` and `/signup`.
While it is set, nobody can see the waitlist form without the passcode, and testers need it too.
To gauge interest publicly, remove the passcode line. Signup stays closed because of waitlist
mode.

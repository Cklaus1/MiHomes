"""Configuration CLI commands."""


import typer
from rich.table import Table

from mihomes import crypto
from mihomes.cli.formatters import console, esc, format_success
from mihomes.db import get_session
from mihomes.services import config_service as config_svc

app = typer.Typer(name="config", help="Manage MiHomes configuration")


@app.command("set")
def set_config(
    key: str = typer.Argument(..., help="Configuration key"),
    value: str | None = typer.Argument(
        None, help="Value. Omit for a secret key and you will be prompted without echo."
    ),
):
    """Set a configuration value. Secrets are prompted for, encrypted, and masked on echo.

    **Two leaks lived in the previous four lines of this command**, both of them the shape Step 15
    fixed everywhere except here:

    1. It echoed `f"{key} = {value}"` unmasked, so setting a bot token printed the credential into
       terminal scrollback — the exact exposure `get` and `list` were hardened against.
    2. It took the value as a positional *argument*, so the credential also landed in shell
       history, where it outlives the terminal session entirely.

    So a secret with no value supplied is now prompted for with `hide_input=True` — the pattern
    `cli/ai.py:573` already used for the same class of value — and the confirmation is masked.
    Passing a secret positionally still works, because scripts do it and breaking them to fix a
    disclosure the operator opted into would be the wrong trade; it just warns.
    """
    is_secret = config_svc.is_secret(key)

    if value is None:
        if is_secret:
            value = typer.prompt(f"{key} (input hidden)", hide_input=True)
        else:
            value = typer.prompt(key)
    elif is_secret:
        console.print(
            "[yellow]Note:[/yellow] a credential passed on the command line is recorded in your "
            "shell history. Omit the value next time to be prompted instead."
        )

    with get_session() as session:
        try:
            config_svc.set_config(session, key, value)
        except crypto.EncryptionUnavailable as exc:
            # Refusing rather than writing plaintext, same decision as the web form makes.
            console.print(f"[red]{esc(str(exc))}[/red]")
            raise typer.Exit(code=1) from exc
        format_success(f"{key} = {config_svc.mask_value(key, value)}")


@app.command("generate-key")
def generate_key():
    """Print a fresh encryption key for `MIHOMES_SECRET_KEY`.

    Prints and does not store: writing the key into the same database it protects would be a
    circular arrangement, and writing it to a dotfile would guess at a deployment layout this
    command cannot see. The operator places it — shell profile locally, systemd unit or
    `fly secrets set` on a server.
    """
    console.print(crypto.generate_key())
    console.print(
        f"[dim]Set {crypto.SECRET_KEY_ENV} to this value in every process that reads a "
        f"credential — the app, the Telegram bot, and scripts/watchdog.py. Store it somewhere "
        f"you will not lose it: without it the encrypted values cannot be recovered.[/dim]"
    )


@app.command("encrypt-secrets")
def encrypt_secrets():
    """Encrypt any credentials still stored as plaintext.

    Run once after setting `MIHOMES_SECRET_KEY` on an existing install. Idempotent, so it is safe
    in a deploy script — an already-encrypted value is skipped rather than double-wrapped.

    Deliberately a command and not a migration: a migration that reads key material from the
    environment produces a different result depending on where it runs, and this phase already hit
    that trap three times. See `config_service.encrypt_existing_secrets`.
    """
    if crypto.secret_key() is None:
        console.print(
            f"[red]{crypto.SECRET_KEY_ENV} is not set.[/red] Run `mihomes config generate-key`, "
            f"put the value in your environment, then run this again."
        )
        raise typer.Exit(code=1)

    with get_session() as session:
        converted = config_svc.encrypt_existing_secrets(session)

    if not converted:
        format_success("nothing to do — every stored credential is already encrypted")
        return
    for key in converted:
        console.print(f"  {esc(key)} [green]encrypted[/green]")
    format_success(f"{len(converted)} credential(s) encrypted")


@app.command("get")
def get_config(
    key: str = typer.Argument(..., help="Configuration key"),
):
    """Get a configuration value. Secrets are masked."""
    with get_session() as session:
        value = config_svc.get_config(session, key)
        if value is not None:
            # SPEC-003 Step 15 — masked on read, here as well as in the web UI. This command
            # printed API keys in full, which is how they end up in terminal scrollback,
            # screenshots and pasted bug reports.
            console.print(f"{esc(key)} = {esc(config_svc.mask_value(key, value))}")
        else:
            console.print(f"[dim]{esc(key)} is not set[/dim]")


@app.command("list")
def list_config():
    """List all configuration values. Secrets are masked."""
    with get_session() as session:
        # `list_config_for_display`, not `list_config`: the unmasked variant still exists for the
        # app paths that need real values, and calling the wrong one here is exactly the mistake
        # this command used to make.
        configs = config_svc.list_config_for_display(session)
        table = Table(title="Configuration")
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_column("Source", style="dim")
        for c in configs:
            table.add_row(esc(c["key"]), esc(c["value"]) or "-", c["source"])
        console.print(table)


@app.command("reset")
def reset_config(
    key: str = typer.Argument(..., help="Configuration key to reset to default"),
):
    """Reset a configuration value to its default."""
    with get_session() as session:
        config_svc.reset_config(session, key)
        default = config_svc.get_config(session, key)
        format_success(f"{key} reset to default ({default})")

"""`npa` CLI for miners."""
from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Never Play Alone subnet CLI")


@app.command()
def submit(
    archive_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    wallet_name: str = typer.Option("default", "--wallet", help="Bittensor wallet name"),
    wallet_hotkey: str = typer.Option("default", "--hotkey", help="Bittensor hotkey name"),
) -> None:
    """Upload a round submission tarball to the backend."""
    from validator import chain
    from validator.api_client import APIClient

    if archive_path.suffixes[-2:] != [".tar", ".gz"]:
        typer.echo("error: submission archive must be a .tar.gz file", err=True)
        raise typer.Exit(2)

    wallet = chain.make_wallet(wallet_name, wallet_hotkey)
    miner_uid = chain.hotkey_uid(wallet.hotkey.ss58_address)
    api = APIClient(wallet)
    try:
        slot = api.create_submission_slot(miner_uid=miner_uid, filename=archive_path.name)
        api.upload_submission_file(slot["upload_url"], archive_path)
        result = api.finalize_submission(slot["submission_id"])
    finally:
        api.close()

    typer.echo(f"submission_id: {result['submission_id']}")
    typer.echo(f"round_id:      {result['round_id']}")
    typer.echo(f"miner_uid:     {result['miner_uid']}")
    typer.echo(f"status:        {result['status']}")
    if result.get("accepted"):
        typer.echo(f"sha256:        {result['sha256']}")
        typer.echo(f"size_bytes:    {result['size_bytes']}")
    else:
        typer.echo(f"rejection:     {result.get('rejection_reason')}")


@app.command()
def status(
    api_url: str = typer.Option(None, "--api", help="Override NPA_API_URL"),
) -> None:
    """Show the current submission round from the backend."""
    import httpx

    from validator.config import API_URL

    url = (api_url or API_URL).rstrip("/")
    try:
        response = httpx.get(f"{url}/miner/rounds/current", timeout=10.0)
        response.raise_for_status()
    except Exception as exc:
        typer.echo(f"failed to reach {url}: {exc}", err=True)
        raise typer.Exit(1)

    data = response.json()
    round_row = data.get("submission_round")
    if not round_row:
        typer.echo("submission_round: (none)")
        return

    typer.echo(f"round_id:             {round_row['round_id']}")
    typer.echo(f"status:               {round_row['status']}")
    typer.echo(f"submission_open_at:   {round_row['submission_open_at']}")
    typer.echo(f"evaluation_start_at:  {round_row['evaluation_start_at']}")
    typer.echo(f"scoreboard_deadline:  {round_row['scoreboard_deadline_at']}")
    typer.echo(f"round_end_at:         {round_row['round_end_at']}")


if __name__ == "__main__":
    app()


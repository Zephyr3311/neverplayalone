"""`npa` CLI for miners.

    npa commit owner/repo@sha [--wallet NAME] [--hotkey NAME]
"""
from __future__ import annotations

import sys

import typer

app = typer.Typer(help="Never Play Alone subnet CLI")


@app.command()
def commit(
    target: str = typer.Argument(..., help="GitHub reference: owner/repo@sha"),
    wallet_name: str = typer.Option("default", "--wallet", help="Bittensor wallet name"),
    wallet_hotkey: str = typer.Option("default", "--hotkey", help="Bittensor hotkey name"),
) -> None:
    """Publish your agent code (owner/repo@sha) on-chain so validators can fetch and run it."""
    if "@" not in target:
        typer.echo("error: target must be in 'owner/repo@sha' format", err=True)
        raise typer.Exit(2)
    repo, sha = target.rsplit("@", 1)
    if "/" not in repo or not sha:
        typer.echo("error: repo must be 'owner/repo' and sha must be non-empty", err=True)
        raise typer.Exit(2)

    import bittensor as bt
    from validator.config import NETUID, NETWORK

    wallet = bt.wallet(name=wallet_name, hotkey=wallet_hotkey)
    subtensor = bt.subtensor(network=NETWORK)

    payload = f"{repo}@{sha}"
    typer.echo(f"committing: {payload}")
    typer.echo(f"hotkey:     {wallet.hotkey.ss58_address}")
    typer.echo(f"netuid:     {NETUID}")
    typer.echo(f"network:    {NETWORK}")

    subtensor.set_commitment(wallet=wallet, netuid=NETUID, data=payload)
    typer.echo("committed.")


@app.command()
def status(
    api_url: str = typer.Option(None, "--api", help="Override NPA_API_URL"),
) -> None:
    """Show the current duel pair from the API."""
    import httpx

    from validator.config import API_URL

    url = (api_url or API_URL).rstrip("/")
    try:
        r = httpx.get(f"{url}/duel/current", timeout=10.0)
        r.raise_for_status()
    except Exception as e:
        typer.echo(f"failed to reach {url}: {e}", err=True)
        raise typer.Exit(1)

    data = r.json()
    typer.echo(f"epoch:      {data.get('epoch_id')}")
    king = data.get("king")
    chal = data.get("challenger")
    if king:
        typer.echo(f"king:       uid={king['uid']} {king['repo']}@{king['sha'][:12]}")
    else:
        typer.echo("king:       (none)")
    if chal:
        typer.echo(f"challenger: uid={chal['uid']} {chal['repo']}@{chal['sha'][:12]}")
    else:
        typer.echo("challenger: (none)")


if __name__ == "__main__":
    app()

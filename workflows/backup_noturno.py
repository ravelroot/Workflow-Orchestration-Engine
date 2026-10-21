"""Rotina de infraestrutura na fila `critical`, com arquivos reais em disco."""

import gzip
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from delco import Workflow

backup = Workflow(
    "backup_noturno",
    description="Gera um snapshot (simulado), compacta, confere a integridade com SHA-256 "
    "e mantém só as cópias mais recentes.",
    schedule="30 2 * * *",
    params={"pasta": "/tmp/delco/backups", "manter": 7, "tamanho_mb": 4},
    tags=["exemplo", "infra"],
    default_queue="critical",
    default_retries=3,
    default_retry_delay=30,
)


def _pasta(ctx) -> Path:
    pasta = Path(ctx.params["pasta"])
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


@backup.task(timeout=600)
def gerar_snapshot(ctx):
    """Cria o arquivo do snapshot (bytes aleatórios simulando um dump)."""
    quando = (ctx.logical_date or datetime.now(UTC)).strftime("%Y%m%d-%H%M")
    caminho = _pasta(ctx) / f"snapshot-{quando}-{ctx.run_id}.bin"
    tamanho = int(ctx.params["tamanho_mb"])
    with caminho.open("wb") as f:
        for i in range(tamanho):
            f.write(os.urandom(1024 * 1024))
            ctx.progress((i + 1) * 100 / tamanho)
    ctx.log(f"Snapshot de {tamanho} MB em {caminho.name}")
    return {"arquivo": str(caminho)}


@backup.task(depends_on=[gerar_snapshot])
def compactar(ctx):
    """Compacta com gzip e apaga o original."""
    origem = Path(ctx.upstream["gerar_snapshot"]["arquivo"])
    destino = origem.with_suffix(".bin.gz")
    with origem.open("rb") as entrada, gzip.open(destino, "wb", compresslevel=6) as saida:
        shutil.copyfileobj(entrada, saida)
    origem.unlink(missing_ok=True)
    ctx.log(f"{destino.name}: {destino.stat().st_size / 1_048_576:.1f} MB")
    return {"arquivo": str(destino), "bytes": destino.stat().st_size}


@backup.task(depends_on=[compactar])
def verificar_integridade(ctx):
    """Lê o arquivo inteiro de volta e calcula o SHA-256."""
    caminho = Path(ctx.upstream["compactar"]["arquivo"])
    h = hashlib.sha256()
    with gzip.open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    ctx.log(f"SHA-256 {h.hexdigest()[:16]}…")
    return {"sha256": h.hexdigest()}


@backup.task(depends_on=[compactar, verificar_integridade])
def registrar_copia(ctx):
    """Acrescenta a cópia ao manifesto da pasta."""
    manifesto = _pasta(ctx) / "manifesto.json"
    itens = json.loads(manifesto.read_text()) if manifesto.exists() else []
    itens.append(
        {
            "arquivo": ctx.upstream["compactar"]["arquivo"],
            "sha256": ctx.upstream["verificar_integridade"]["sha256"],
            "execucao": ctx.run_id,
        }
    )
    manifesto.write_text(json.dumps(itens, indent=2))
    return {"copias": len(itens)}


@backup.task(depends_on=[registrar_copia])
def limpar_antigas(ctx):
    """Apaga as cópias além das N mais recentes."""
    copias = sorted(_pasta(ctx).glob("snapshot-*.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    removidas = copias[int(ctx.params["manter"]) :]
    for p in removidas:
        p.unlink(missing_ok=True)
    ctx.log(f"{len(removidas)} cópia(s) antiga(s) removida(s)")
    return {"removidas": len(removidas)}

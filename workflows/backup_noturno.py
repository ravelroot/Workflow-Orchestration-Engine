import gzip
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from delco import Param, Workflow

backup = Workflow(
    "backup_noturno",
    description="Gera um snapshot (simulado), compacta, confere a integridade com SHA-256 "
    "e mantém só as cópias mais recentes.",
    schedule="30 2 * * *",
    params={
        "pasta": Param("/tmp/delco/backups", description="Onde as cópias ficam"),
        "manter": Param(7, min=1, max=90, description="Quantas cópias recentes manter"),
        "tamanho_mb": Param(4, min=1, max=1024, description="Tamanho do snapshot simulado"),
    },
    run_timeout=3600,
    tags=["exemplo", "infra"],
    default_queue="critical",
    default_retries=3,
    default_retry_delay=30,
)


def _pasta(ctx) -> Path:
    pasta = Path(ctx.params["pasta"])
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


@backup.task(timeout=600, description="Cria o arquivo do snapshot (bytes aleatórios simulando um dump).")
def gerar_snapshot(ctx):
    quando = (ctx.logical_date or datetime.now(UTC)).strftime("%Y%m%d-%H%M")
    caminho = _pasta(ctx) / f"snapshot-{quando}-{ctx.run_id}.bin"
    tamanho = int(ctx.params["tamanho_mb"])
    with caminho.open("wb") as f:
        for i in range(tamanho):
            f.write(os.urandom(1024 * 1024))
            ctx.progress((i + 1) * 100 / tamanho)
    ctx.log(f"Snapshot de {tamanho} MB em {caminho.name}")
    return {"arquivo": str(caminho)}


@backup.task(depends_on=[gerar_snapshot], description="Compacta com gzip e apaga o original.")
def compactar(ctx):
    origem = Path(ctx.upstream["gerar_snapshot"]["arquivo"])
    destino = origem.with_suffix(".bin.gz")
    with origem.open("rb") as entrada, gzip.open(destino, "wb", compresslevel=6) as saida:
        shutil.copyfileobj(entrada, saida)
    origem.unlink(missing_ok=True)
    ctx.log(f"{destino.name}: {destino.stat().st_size / 1_048_576:.1f} MB")
    return {"arquivo": str(destino), "bytes": destino.stat().st_size}


@backup.task(depends_on=[compactar], description="Lê o arquivo inteiro de volta e calcula o SHA-256.")
def verificar_integridade(ctx):
    caminho = Path(ctx.upstream["compactar"]["arquivo"])
    h = hashlib.sha256()
    with gzip.open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    ctx.log(f"SHA-256 {h.hexdigest()[:16]}…")
    return {"sha256": h.hexdigest()}


@backup.task(depends_on=[compactar, verificar_integridade], description="Acrescenta a cópia ao manifesto da pasta.")
def registrar_copia(ctx):
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


@backup.task(depends_on=[registrar_copia], description="Apaga as cópias além das N mais recentes.")
def limpar_antigas(ctx):
    copias = sorted(_pasta(ctx).glob("snapshot-*.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    removidas = copias[int(ctx.params["manter"]) :]
    for p in removidas:
        p.unlink(missing_ok=True)
    ctx.log(f"{len(removidas)} cópia(s) antiga(s) removida(s)")
    return {"removidas": len(removidas)}

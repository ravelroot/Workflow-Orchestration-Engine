"""Descobre arquivos de workflow numa pasta e importa cada um de forma isolada."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from .sdk import Workflow, WorkflowError


@dataclass
class LoadResult:
    workflows: dict[str, Workflow] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def module_name_for(path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:10]
    return f"delco_workflow_{path.stem}_{digest}"


def import_file(path: Path) -> ModuleType:
    """Importa um arquivo .py pelo caminho, sempre lendo a versão atual do disco."""
    path = path.resolve()
    name = module_name_for(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Não foi possível importar {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    added = str(path.parent) not in sys.path
    if added:
        sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    finally:
        if added:
            sys.path.remove(str(path.parent))
    return module


def workflows_in(module: ModuleType) -> list[Workflow]:
    return [v for v in vars(module).values() if isinstance(v, Workflow)]


def workflow_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.rglob("*.py") if not p.name.startswith(("_", ".")) and "__pycache__" not in p.parts
    )


def load_folder(folder: Path) -> LoadResult:
    result = LoadResult()
    for path in workflow_files(folder):
        rel = str(path.relative_to(folder))
        try:
            module = import_file(path)
            for wf in workflows_in(module):
                wf.source_file = rel
                wf.validate()
                if wf.name in result.workflows:
                    raise WorkflowError(
                        f"O workflow {wf.name!r} já foi definido em {result.workflows[wf.name].source_file}."
                    )
                result.workflows[wf.name] = wf
        except WorkflowError as exc:
            result.errors[rel] = str(exc)
        except Exception:  # erro de sintaxe ou de importação no arquivo do usuário
            result.errors[rel] = traceback.format_exc(limit=4)
    return result


def find_task(folder: Path, source_file: str, workflow: str, task: str):
    """Importa o arquivo do workflow e devolve a função da tarefa (usado no processo filho)."""
    path = (folder / source_file).resolve()
    if not path.is_file():
        raise WorkflowError(f"Arquivo do workflow não encontrado: {source_file}")
    module = import_file(path)
    for wf in workflows_in(module):
        if wf.name == workflow:
            if task not in wf.tasks:
                raise WorkflowError(f"A tarefa {task!r} não existe mais em {workflow} ({source_file}).")
            return wf.tasks[task].fn
    raise WorkflowError(f"O workflow {workflow!r} não está mais em {source_file}.")

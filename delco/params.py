from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from .errors import WorkflowError

TYPES = ("string", "integer", "number", "boolean", "date", "array", "object")
_ALIASES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    date: "date",
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "text": "string",
}
_MISSING = object()
_TRUE = {"1", "true", "sim", "yes", "on", "s", "y"}
_FALSE = {"0", "false", "nao", "não", "no", "off", "n"}


class ParamError(WorkflowError):
    pass


class Param:
    def __init__(
        self,
        default: Any = _MISSING,
        *,
        type: Any = None,
        description: str = "",
        label: str | None = None,
        choices: list[Any] | tuple[Any, ...] | None = None,
        min: float | None = None,
        max: float | None = None,
        required: bool | None = None,
        secret: bool = False,
    ) -> None:
        self.has_default = default is not _MISSING
        self.default = None if default is _MISSING else default
        self.type = _resolve_type(type, self.default if self.has_default else None, choices)
        self.description = (description or "").strip()
        self.label = label
        self.choices = list(choices) if choices is not None else None
        self.min = min
        self.max = max
        self.required = (not self.has_default) if required is None else bool(required)
        self.secret = bool(secret)
        if self.choices is not None and not self.choices:
            raise ParamError("choices precisa ter pelo menos uma opção.")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ParamError("min não pode ser maior que max.")
        if self.has_default and self.default is not None:
            self.default = coerce(self.schema(), self.default, "padrão")

    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "required": self.required}
        if self.has_default:
            out["default"] = self.default.isoformat() if isinstance(self.default, date) else self.default
        for key in ("description", "label", "choices", "min", "max"):
            value = getattr(self, key)
            if value not in (None, ""):
                out[key] = value
        if self.secret:
            out["secret"] = True
        return out

    def __repr__(self) -> str:
        return f"Param({self.type}, default={self.default!r})"


def _resolve_type(kind: Any, default: Any, choices: Any) -> str:
    if kind is None:
        sample = default if default is not None else (list(choices)[0] if choices else None)
        if isinstance(sample, bool):
            return "boolean"
        if isinstance(sample, int):
            return "integer"
        if isinstance(sample, float):
            return "number"
        if isinstance(sample, list | tuple):
            return "array"
        if isinstance(sample, dict):
            return "object"
        if isinstance(sample, date):
            return "date"
        return "string"
    resolved = _ALIASES.get(kind, kind)
    if resolved not in TYPES:
        raise ParamError(f"Tipo de parâmetro desconhecido: {kind!r}. Use {', '.join(TYPES)}.")
    return resolved


def infer(value: Any) -> dict[str, Any]:
    if isinstance(value, Param):
        return value.schema()
    out = Param(value).schema()
    out["inferred"] = True
    return out


def coerce(schema: dict[str, Any], value: Any, name: str = "valor") -> Any:
    kind = schema.get("type", "string")
    if value is None:
        if schema.get("required") and "default" not in schema:
            raise ParamError("Obrigatório.")
        return None
    try:
        if kind == "string":
            if isinstance(value, dict | list):
                raise ParamError("Precisa ser um texto.")
            value = str(value)
        elif kind == "integer":
            if isinstance(value, bool):
                raise ParamError("Precisa ser um número inteiro.")
            if isinstance(value, float):
                if not value.is_integer():
                    raise ParamError("Precisa ser um número inteiro.")
                value = int(value)
            elif isinstance(value, str):
                value = int(value.strip())
            elif not isinstance(value, int):
                raise ParamError("Precisa ser um número inteiro.")
        elif kind == "number":
            if isinstance(value, bool):
                raise ParamError("Precisa ser um número.")
            if isinstance(value, str):
                value = float(value.strip().replace(",", "."))
            elif not isinstance(value, int | float):
                raise ParamError("Precisa ser um número.")
        elif kind == "boolean":
            if isinstance(value, str):
                low = value.strip().lower()
                if low in _TRUE:
                    value = True
                elif low in _FALSE:
                    value = False
                else:
                    raise ParamError("Use true ou false.")
            elif not isinstance(value, bool):
                raise ParamError("Use true ou false.")
        elif kind == "date":
            if isinstance(value, datetime):
                value = value.date().isoformat()
            elif isinstance(value, date):
                value = value.isoformat()
            else:
                value = date.fromisoformat(str(value).strip()[:10]).isoformat()
        elif kind == "array":
            if isinstance(value, str):
                text = value.strip()
                if text.startswith("["):
                    value = json.loads(text)
                else:
                    value = [v.strip() for v in text.split(",") if v.strip()]
            if isinstance(value, tuple):
                value = list(value)
            if not isinstance(value, list):
                raise ParamError("Precisa ser uma lista.")
        elif kind == "object":
            if isinstance(value, str):
                value = json.loads(value)
            if not isinstance(value, dict):
                raise ParamError("Precisa ser um objeto JSON.")
    except ParamError:
        raise
    except (ValueError, TypeError):
        labels = {
            "integer": "um número inteiro",
            "number": "um número",
            "date": "uma data no formato AAAA-MM-DD",
            "array": "uma lista",
            "object": "um objeto JSON",
        }
        raise ParamError(f"Precisa ser {labels.get(kind, 'um valor válido')}.") from None
    choices = schema.get("choices")
    if choices is not None and value not in choices:
        raise ParamError("Escolha uma das opções: " + ", ".join(str(c) for c in choices) + ".")
    if kind in ("integer", "number"):
        if schema.get("min") is not None and value < schema["min"]:
            raise ParamError(f"Precisa ser no mínimo {schema['min']:g}.")
        if schema.get("max") is not None and value > schema["max"]:
            raise ParamError(f"Precisa ser no máximo {schema['max']:g}.")
    if kind in ("string", "array"):
        if schema.get("min") is not None and len(value) < schema["min"]:
            raise ParamError(f"Precisa ter pelo menos {_count(schema['min'], kind)}.")
        if schema.get("max") is not None and len(value) > schema["max"]:
            raise ParamError(f"Pode ter no máximo {_count(schema['max'], kind)}.")
    return value


def _count(n: float, kind: str) -> str:
    one, many = ("caractere", "caracteres") if kind == "string" else ("item", "itens")
    return f"{n:g} {one if n == 1 else many}"


def resolve(
    schema: dict[str, dict[str, Any]], defaults: dict[str, Any], given: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, str]]:
    given = dict(given or {})
    merged = dict(defaults or {})
    errors: dict[str, str] = {}
    for name, spec in (schema or {}).items():
        if name in given:
            value = given.pop(name)
        elif "default" in spec:
            value = spec["default"]
        else:
            value = None
        if value is None and spec.get("required"):
            errors[f"params.{name}"] = "Obrigatório."
            continue
        try:
            merged[name] = coerce(spec, value, name)
        except ParamError as exc:
            if spec.get("inferred"):
                merged[name] = value
            else:
                errors[f"params.{name}"] = str(exc)
    merged.update(given)
    return merged, errors

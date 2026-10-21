#!/usr/bin/env python
"""Utilitário de linha de comando do Django (o comando `delco` faz o mesmo de forma mais curta)."""

import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delco.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)

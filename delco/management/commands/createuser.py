import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from delco.accounts import ValidationError, create_user


class Command(BaseCommand):
    help = "Cria um usuário do painel."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--role", choices=["admin", "operator", "viewer"], default="operator")
        parser.add_argument("--password", help="senha (se omitida, é pedida no terminal)")

    def handle(self, *args, email, name, role, password=None, **options):
        password = password or os.environ.get("DELCO_PASSWORD") or getpass.getpass("Senha do novo usuário: ")
        try:
            user = create_user({"email": email, "name": name, "role": role, "password": password})
        except ValidationError as exc:
            raise CommandError("\n".join(f"{k}: {v}" for k, v in exc.fields.items())) from None
        self.stdout.write(f"Usuário {user['email']} criado com o papel {user['role']}.")

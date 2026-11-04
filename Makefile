.PHONY: ajuda subir parar logs testes lint validar doutor exemplo novo pdfs

ajuda:
	@echo "make subir | parar | logs | testes | lint | validar | doutor | exemplo | novo NOME=meu_workflow | pdfs"

subir:
	./iniciar.sh

parar:
	docker compose down

logs:
	docker compose logs -f --tail=100

testes:
	python -m pytest

lint:
	ruff check .
	for f in web/js/*.js web/js/pages/*.js; do node --check "$$f"; done

validar:
	delco validate workflows

doutor:
	docker compose exec api delco doctor

exemplo:
	delco dev run ola_delco -p nome=Delco

novo:
	delco new $(NOME)

pdfs:
	python docs/fonte/gerar.py

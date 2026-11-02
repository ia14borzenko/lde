# Makefile — короткие обёртки (основной интерфейс — bin/lde).
SHELL := /bin/bash
LDE := ./bin/lde
PY := toolchain/venv/bin/python

.PHONY: help install doctor test unit smoke screens skill

help:    ## показать цели
	@sed -n 's/^\([a-z-]*\):.*## \(.*\)/\1 — \2/p' Makefile

install: ## установить/проверить инструменты
	$(LDE) doctor --fix

doctor:  ## диагностика без установки
	$(LDE) doctor

unit:    ## юнит-тесты (разбор формата, числа, типографика)
	$(PY) -m unittest tests/unit/test_core.py

smoke:   ## сквозной сценарий на шаблоне lde init
	$(PY) tests/smoke.py

test: unit smoke ## быстрые тесты
	$(PY) tests/test_conflict.py
	$(PY) tests/ui/test_formula_inserts.py

screens: ## скриншоты Studio на демо-проекте → docs/screens
	$(PY) tests/ui/screens.py

skill:   ## поставить/проверить скилл агента lde
	toolchain/install-skill.sh
	$(LDE) skill-status

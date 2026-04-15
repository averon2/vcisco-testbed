.PHONY: up down plan seed status cost init fmt validate dashboard

TF = terraform -chdir=terraform

init:
	$(TF) init

plan: init
	$(TF) plan

up: init
	$(TF) apply -auto-approve
	@echo ""
	@echo "── Paste these into vcisco's Connect AWS flow ──"
	@$(TF) output

down:
	$(TF) destroy -auto-approve
	@echo ""
	@echo "Environment torn down. Billing is back to \$$0."

status:
	$(TF) output

seed:
	python synthetic/publish.py

dashboard:
	@echo "Starting dashboard at http://127.0.0.1:5050"
	python dashboard/app.py

fmt:
	$(TF) fmt -recursive

validate: init
	$(TF) validate

cost:
	@echo "Rough cost while 'up':"
	@echo "  - 2 x t3.micro (free tier first 12mo, else ~\$$7.50 each)"
	@echo "  - S3 inventory bucket: pennies"
	@echo "  - NAT gateway: none (public subnet only)"
	@echo "  - Windows box: disabled unless enable_windows=true (+\$$20/mo)"
	@echo ""
	@echo "Run 'make down' to destroy everything and return to \$$0."

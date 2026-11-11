.PHONY: demo test

# make demo ARGS="--stop veh-09"
demo:
	./scripts/demo.sh $(ARGS)

test:
	uv run --env-file .env pytest -v

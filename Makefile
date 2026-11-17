.PHONY: env
env:
	uv run scripts/gcx_to_env.py $(ARGS)

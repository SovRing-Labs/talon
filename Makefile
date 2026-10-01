.PHONY: test clean

# Full suite. Tests that need Omnitool skip unless FLEET_EXEC points at its bin/fleet-exec
# (git clone https://github.com/SovRing-Labs/omnitool; export FLEET_EXEC=$$PWD/omnitool/bin/fleet-exec).
test:
	PYTHONPATH=src pytest -q -rs tests/
	@rm -rf data
	@echo "All Talon release tests PASSED."

clean:
	rm -rf data __pycache__ tests/__pycache__ src/__pycache__ src/engine/__pycache__ .pytest_cache

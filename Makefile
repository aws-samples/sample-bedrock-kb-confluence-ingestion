.PHONY: security-scan bandit pip-audit audit lock install

# Run all security scans (bandit SAST + pip-audit SCA)
security-scan: bandit pip-audit

# Static Application Security Testing — fail on HIGH+ severity findings
bandit:
	bandit -r src/ckn_ingestion/ -ll

# Software Composition Analysis — fail on any known vulnerability
pip-audit:
	pip-audit

# Alias: run pip-audit at HIGH level (mirrors npm audit --audit-level=high)
audit:
	pip-audit --desc --fix --dry-run

# Regenerate requirements.lock with pinned versions and SHA-256 hashes
lock:
	pip-compile --generate-hashes --output-file=requirements.lock pyproject.toml

# Install dependencies with hash verification (supply chain protection)
install:
	pip install --require-hashes -r requirements.lock

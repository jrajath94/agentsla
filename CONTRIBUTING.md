# Contributing to AgentSLA

## Commit Standards

All commits must follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `perf`: Performance improvement
- `test`: Test additions or fixes
- `docs`: Documentation
- `refactor`: Code refactoring without behavioral change
- `bench`: Benchmark changes
- `ci`: CI/CD pipeline changes

**Subject:**
- Imperative mood ("add" not "added")
- No period at end
- ≤72 characters
- Lowercase

## Code Quality

All code must pass:

```bash
# Formatting
ruff format .

# Linting
ruff check .

# Type checking (strict on core, policy, verify)
mypy agentsla/core agentsla/policy agentsla/verify

# Testing (≥85% coverage on core modules)
pytest tests/ --cov=agentsla/core --cov-fail-under=85
```

## Pull Request Process

1. **Branch:** Create from `main` with name `feat/description` or `fix/description`
2. **Commits:** Atomic, conventional format
3. **Tests:** All new code must have tests
4. **Coverage:** Do not decrease coverage
5. **CI:** All workflows must pass before merge
6. **Description:** Clear problem statement, approach, and tradeoffs

## Code Style

- Python 3.11+
- Type hints on all public APIs
- Docstrings on public classes/functions (one-line preferred unless complex)
- No `TODO` comments without associated issue numbers

## Testing

Write tests that cover:
- Happy path
- Edge cases (empty inputs, boundary conditions)
- Error conditions

Use `pytest` fixtures for common setup.

## Release

Releases follow [semantic versioning](https://semver.org/). Maintainers tag releases on `main`.

## Questions?

Open an issue or discussion on GitHub.

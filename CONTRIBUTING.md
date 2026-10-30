# Contributing

## Read first

`CONTEXT.md`, then `docs/SKETCH.md`, then `app/src/returns_desk/types.py`. The glossary wins
on names, the sketch wins on behavior, and the contracts in `types.py` win on signatures.
Change `types.py` only when a contract is genuinely wrong, and say why in the pull request.

## Setup

Python 3.12 or later, managed with [uv](https://docs.astral.sh/uv/): `cd app && uv sync`,
then `uv run pytest`. Use `uv add` for dependencies; there is no `requirements.txt`.

Tests never call Azure. The engines used in tests return canned answers keyed by request id
from the sample set; they live in `app/tests/conftest.py`, not in the package. Tests also fix
the desk's date, so they do not depend on the calendar.

## Test first

Every change is made red, green, refactor:

1. Write the tests for the behavior first, in `app/tests/test_<module>.py`, and run them. They
   must fail because the code does not do it yet.
2. Write the smallest change that makes them pass.
3. Tidy, and run the whole suite.

The seven requests in the sketch's demo queue (section 8) are the acceptance tests for the
desk flow, one test per row. A change that alters any of them changes the demo, so update the
sketch in the same pull request.

## Vocabulary

Code, comments, UI text and docs use the glossary's terms. Avoid: fault, agent (except in
"Open Policy Agent"), decision model, decision engine, LLM, tier, stand-in, mock, outcome,
ground truth, gold set. Each glossary entry lists the words it replaces.

## Infrastructure

`infra/` and `infra/hosting/` are OpenTofu. Never commit `*.tfstate*`, `*.tfplan` or
`terraform.tfvars`; `.gitignore` covers them. Run `tofu validate` in both folders before a
pull request; changes to the app never need an apply.

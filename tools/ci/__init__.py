"""tools/ci — the CI Control Registry validator (ADR-0100).

Dedicated to CI governance and deliberately SEPARATE from tools/arch (architecture governance):
they share method (a declarative source of truth + negative controls that prove the checks are not
decorative), NOT ownership. Nothing here imports tools/arch and nothing there imports this.
"""

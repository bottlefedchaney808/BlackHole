"""One-off endpoint diagnostics — see README.md in this folder.

Makes `diagnostics` a package so these can be run as
`python -m diagnostics.<name>` from the project root, which puts the root
on sys.path and keeps their `from thetadata_client import ...` working.
"""

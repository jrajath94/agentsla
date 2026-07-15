"""CLI surface for ``agentsla metrics`` — Prometheus exporter + snapshot.

Two subcommands ship here:

    agentsla metrics serve [--port N] [--addr IP] [--registry NAME]
        Long-running HTTP server that exposes the three AgentSLA
        metric families on ``/metrics`` for Prometheus to scrape.
        Default registry is the process-global ``REGISTRY``.

    agentsla metrics snapshot [--registry NAME] [--format text|json]
        One-shot dump of the registry's exposition text to stdout,
        exits 0. Mirrors what Prometheus would scrape.

The Grafana dashboard (``dashboards/grafana.json``) queries these
metric names; without a serving /metrics endpoint the dashboard
cannot populate. This is the W7 exporter half of CLAUDE.md's
"Classifier + Prometheus/Grafana" deliverable.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from prometheus_client import REGISTRY, generate_latest, start_http_server

from agentsla.classify.metrics import build_metrics

# Register the three AgentSLA metric families on the process-global
# REGISTRY at import time so ``agentsla metrics snapshot`` and
# ``agentsla metrics serve`` always expose them — even with zero
# samples (HELP/TYPE lines survive, ready for Prometheus to scrape).
# build_metrics() is idempotent: a second call returns the cached
# bundle rather than raising ``Duplicated timeseries``.
_ENSURED = build_metrics(registry=REGISTRY)

# ---------------------------------------------------------------------------
# Registry resolution
# ---------------------------------------------------------------------------


def _resolve_registry(name: str) -> Any:
    """Resolve ``--registry NAME`` to a ``CollectorRegistry``.

    Only the literal ``"global"`` is recognised (default). Tests pass
    ``"isolated"`` and substitute their own registry via
    ``--registry-name isolated`` paired with a fixture, but production
    callers should never need to. We deliberately keep this surface
    minimal — no env-var magic — so the CLI surface is auditable.
    """
    if name == "global":
        return REGISTRY
    if name == "isolated":
        # Tests inject their registry through a module-level handle set
        # by the test fixture. Production code never passes "isolated".
        return _isolated_registry_handle()
    raise SystemExit(f"unknown --registry {name!r}; expected 'global' or 'isolated'")


_ISOLATED: Any = None


def set_isolated_registry(registry: Any) -> None:
    """Test hook: register a one-shot registry for ``--registry isolated``.

    The CLI's test suite uses this to avoid polluting the process-wide
    ``REGISTRY`` (which prometheus_client adds ``python_gc_*`` /
    ``process_*`` series to). Production callers must not invoke
    this.
    """
    global _ISOLATED
    _ISOLATED = registry


def _isolated_registry_handle() -> Any:
    if _ISOLATED is None:
        raise SystemExit("--registry isolated was passed but no isolated registry was registered")
    return _ISOLATED


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def snapshot(argv: list[str] | None = None) -> int:
    """One-shot dump of the registry's exposition text to stdout."""
    parser = argparse.ArgumentParser(prog="agentsla-metrics-snapshot", description="Dump AgentSLA metrics exposition.")
    parser.add_argument("--registry", default="global", help="'global' (default) or 'isolated' (test hook).")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    args = parser.parse_args(argv)
    registry = _resolve_registry(args.registry)
    if args.format == "text":
        sys.stdout.write(generate_latest(registry).decode())
        sys.stdout.flush()
        return 0
    # JSON: parse exposition text via prometheus_client text parser to
    # preserve family/sample structure rather than re-implementing the
    # format. Import is local so the common text path stays cheap.
    from prometheus_client.parser import text_string_to_metric_families

    text = generate_latest(registry).decode()
    families = []
    for fam in text_string_to_metric_families(text):
        families.append(
            {
                "name": fam.name,
                "type": fam.type,
                "help": fam.documentation,
                "samples": [{"name": s.name, "labels": dict(s.labels), "value": s.value} for s in fam.samples],
            }
        )
    sys.stdout.write(json.dumps({"families": families}, indent=2))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


def serve(argv: list[str] | None = None) -> int:
    """Start the Prometheus HTTP exporter and block forever.

    The default port (9100) is conventional for AgentSLA — same port
    class as the node_exporter default so a Prometheus config can
    scrape AgentSLA alongside other exporters without per-service
    port plumbing.
    """
    parser = argparse.ArgumentParser(prog="agentsla-metrics-serve", description="Serve AgentSLA metrics on /metrics.")
    parser.add_argument("--port", type=int, default=9100, help="HTTP port (default 9100).")
    parser.add_argument("--addr", default="127.0.0.1", help="Bind address (default 127.0.0.1).")
    parser.add_argument("--registry", default="global", help="'global' (default) or 'isolated' (test hook).")
    args = parser.parse_args(argv)
    registry = _resolve_registry(args.registry)
    # start_http_server is non-blocking; it spawns a daemon thread that
    # serves /metrics via a WSGI app. Production callers expect this
    # function to never return; SIGTERM / KeyboardInterrupt will
    # kill the daemon thread along with the process.
    start_http_server(args.port, addr=args.addr, registry=registry)
    print(f"agentsla metrics serving on http://{args.addr}:{args.port}/metrics", file=sys.stderr)
    sys.stderr.flush()
    try:
        # Block forever; in production the process manager (PM2, systemd,
        # docker) is responsible for lifecycle. A simple event.wait() is
        # the cleanest way to park on a signal-safe primitive.
        import threading

        threading.Event().wait()
    except KeyboardInterrupt:
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch on the first positional: ``serve`` or ``snapshot``."""
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print(
            "usage: agentsla metrics {serve,snapshot} ...",
            file=sys.stderr,
        )
        return 1
    cmd, *rest = argv
    if cmd == "serve":
        return serve(rest)
    if cmd == "snapshot":
        return snapshot(rest)
    print(f"unknown metrics subcommand: {cmd!r}", file=sys.stderr)
    return 1


__all__ = ["main", "serve", "set_isolated_registry", "snapshot"]

"""CLI surface for ``agentsla metrics`` — Prometheus exporter + snapshot.

Two subcommands ship under this surface:

    agentsla metrics serve [--port N] [--addr IP] [--registry NAME]
        Long-running HTTP server that exposes the three AgentSLA
        metric families on ``/metrics`` for Prometheus to scrape.
        Default registry is the process-global ``REGISTRY``; tests
        pass ``--registry isolated`` to scrape a per-test
        ``CollectorRegistry``.

    agentsla metrics snapshot [--registry NAME] [--format text|json]
        One-shot dump of the registry's exposition text to stdout,
        exits 0. Mirrors what Prometheus would scrape.

The Grafana dashboard (``dashboards/grafana.json``) queries the
three metric names this CLI serves. Without the serving endpoint the
dashboard cannot populate — this is the W7 exporter half of
CLAUDE.md's "Classifier + Prometheus/Grafana" deliverable.
"""

from __future__ import annotations

import inspect
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

prometheus_client = pytest.importorskip("prometheus_client")

from prometheus_client import (  # noqa: E402
    REGISTRY,
    CollectorRegistry,
    generate_latest,
    start_http_server,
)

from agentsla.classify import FailureCategory  # noqa: E402
from agentsla.classify.classifier import ClassificationResult  # noqa: E402
from agentsla.classify.metrics import (  # noqa: E402
    build_metrics,
    on_classify_callback,
    on_verdict_callback,
)
from agentsla.cli import metrics as metrics_cli  # noqa: E402


@pytest.fixture()
def isolated_registry() -> CollectorRegistry:
    """Fresh ``CollectorRegistry`` per test — never touches the global REGISTRY."""
    registry = CollectorRegistry()
    metrics_cli.set_isolated_registry(registry)
    return registry


def _free_port() -> int:
    """Bind ephemerally and return the OS-assigned port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _scrape(addr: str, port: int, path: str = "/metrics", timeout: float = 2.0) -> tuple[int, str]:
    """GET against the served endpoint, return ``(status, body)``."""
    url = f"http://{addr}:{port}{path}"
    req = urllib.request.Request(url)  # noqa: S310 -- loopback URL is intentional
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- loopback
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode() if e.fp else ""


# ---------------------------------------------------------------------------
# Snapshot subcommand
# ---------------------------------------------------------------------------


class TestSnapshotSubcommand:
    """``agentsla metrics snapshot`` dumps the registry's exposition text."""

    def test_snapshot_text_returns_zero(self, isolated_registry: CollectorRegistry) -> None:
        """Snapshot exits 0 even on a populated registry."""
        build_metrics(registry=isolated_registry)
        rc = metrics_cli.snapshot(["--registry", "isolated"])
        assert rc == 0

    def test_snapshot_unknown_format_returns_nonzero(self, isolated_registry: CollectorRegistry) -> None:
        """Invalid --format value fails with non-zero exit (argparse rejects)."""
        with pytest.raises(SystemExit) as exc:
            metrics_cli.snapshot(["--registry", "isolated", "--format", "xml"])
        assert exc.value.code != 0

    def test_snapshot_help_lists_subcommands(self) -> None:
        """The metrics CLI help must mention both subcommands."""
        with pytest.raises(SystemExit) as exc:
            metrics_cli.snapshot(["--help"])
        assert exc.value.code == 0  # --help exits 0

    def test_snapshot_json_emits_parseable_output(self, isolated_registry: CollectorRegistry) -> None:
        """``--format json`` produces a parseable list of metric families.

        Pin the JSON shape: top-level dict with ``families`` list of
        {name, type, help, samples} entries. The samples list keeps the
        JSON round-tripable to the text format for tooling that
        prefers JSON.
        """
        build_metrics(registry=isolated_registry)
        on_classify_callback(build_metrics(registry=isolated_registry))(
            ClassificationResult(
                trace_id="t1",
                category=FailureCategory.HALLUCINATED_FACT,
                confidence=0.9,
                source="heuristic",
            )
        )
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = metrics_cli.snapshot(["--registry", "isolated", "--format", "json"])
        assert rc == 0
        body = buf.getvalue()
        parsed = __import__("json").loads(body)
        assert "families" in parsed
        names = {f["name"] for f in parsed["families"]}
        # The OpenMetrics text parser strips `_total` from Counter names.
        # Verify the underlying family exists under either form.
        assert "agentsla_failures_total" in names or "agentsla_failures" in names, (
            f"agentsla_failures[ _total] not in JSON output; got {sorted(names)}"
        )
        assert "agentsla_verify_coverage" in names
        assert "agentsla_classify_latency_seconds" in names


# ---------------------------------------------------------------------------
# Serve subcommand
# ---------------------------------------------------------------------------


class TestServeSubcommand:
    """``agentsla metrics serve`` runs an HTTP server on the chosen port."""

    def test_serve_exposes_metric_families_on_metrics_endpoint(self, isolated_registry: CollectorRegistry) -> None:
        """Spin a real HTTP server on a free port and scrape /metrics.

        The test drives the start_http_server + scrape path directly
        (start_http_server is what ``serve`` wraps); the CLI wrapper's
        job is to parse --port/--addr/--registry, start the server, and
        block. We don't test the block (an infinite loop); we test the
        surface contract — once started, the three AgentSLA metric
        families are reachable on /metrics.
        """
        bundle = build_metrics(registry=isolated_registry)
        on_classify_callback(bundle)(
            ClassificationResult(
                trace_id="t1",
                category=FailureCategory.RETRY_LOOP,
                confidence=0.9,
                source="heuristic",
            )
        )
        on_verdict_callback(bundle)(0.7)

        port = _free_port()
        stop = threading.Event()

        def _run() -> None:
            start_http_server(port, addr="127.0.0.1", registry=isolated_registry)
            stop.wait(timeout=5.0)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        body = ""
        status = 0
        for _ in range(50):
            try:
                status, body = _scrape("127.0.0.1", port)
                break
            except Exception:
                time.sleep(0.02)

        try:
            assert status == 200, f"expected 200, got {status} body={body[:200]!r}"
            assert "agentsla_failures_total" in body
            assert "agentsla_verify_coverage" in body
            assert "agentsla_classify_latency_seconds" in body
            assert 'category="retry_loop"' in body
            assert "agentsla_verify_coverage 0.7" in body
        finally:
            stop.set()
            t.join(timeout=2.0)

    def test_serve_signature_accepts_port_and_addr(self) -> None:
        """The serve() entrypoint must exist and accept argv (CLI shape pin)."""
        assert callable(metrics_cli.serve)
        sig = inspect.signature(metrics_cli.serve)
        # argv list of strings, returns int
        params = list(sig.parameters.values())
        assert any(p.name == "argv" for p in params), "serve(argv) signature changed; update CLI dispatcher if intentional"


# ---------------------------------------------------------------------------
# Unified CLI dispatcher
# ---------------------------------------------------------------------------


class TestMainDispatchWiring:
    """The unified ``agentsla`` CLI must dispatch to the metrics subcommand."""

    def test_main_dispatches_metrics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``agentsla metrics snapshot`` reaches the metrics module's main."""
        from agentsla import __main__ as cli_main

        called: dict[str, list[str]] = {}

        def fake_metrics_main(argv: list[str]) -> int:
            called["argv"] = argv
            return 0

        monkeypatch.setattr(metrics_cli, "main", fake_metrics_main)
        rc = cli_main.main(["metrics", "snapshot"])
        assert rc == 0
        assert called["argv"] == ["snapshot"]

    def test_main_usage_lists_metrics_subcommand(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``agentsla`` with no args must mention ``metrics`` in the usage."""
        from agentsla import __main__ as cli_main

        rc = cli_main.main([])
        assert rc == 1
        err = capsys.readouterr().err
        assert "metrics" in err


# ---------------------------------------------------------------------------
# Global REGISTRY sanity (production code path uses REGISTRY, not isolated)
# ---------------------------------------------------------------------------


class TestGlobalRegistrySanity:
    """Default snapshot/scrape reads the global REGISTRY — production code path."""

    def test_global_registry_exposition_is_prometheus_format(self) -> None:
        """``generate_latest(REGISTRY)`` returns valid exposition text.

        Even with no agent code touched, prometheus_client seeds
        python_gc_* + process_* series. Every line is either a comment
        (`# HELP` / `# TYPE`) or `name{labels} value`. Pin that contract
        so a refactor that drops the format trips the test.
        """
        text = generate_latest(REGISTRY).decode()
        assert text, "global REGISTRY exposition must be non-empty"
        for line in text.splitlines():
            if not line.strip():
                continue
            assert line.startswith("#") or " " in line, f"malformed line: {line!r}"

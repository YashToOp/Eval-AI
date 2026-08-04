#!/usr/bin/env python3
"""Local web app for ai-text-eval.

    python serve.py               # then open http://127.0.0.1:8000
    python serve.py --port 9000
    python serve.py --no-browser

Serves a single-page UI and a small JSON API on top of the same engine the
CLI uses. Standard library only — no framework, no build step, no network
calls. Nothing you paste leaves your machine.

Binds to 127.0.0.1 by default. The server evaluates text and nothing else,
but it is a development server with no authentication, so exposing it on a
public interface would let anyone on the network submit work to your CPU.
Use --host deliberately.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ai_text_eval.attacks import apply_attack, attack_names  # noqa: E402
from ai_text_eval.conformal import calibrate, load_calibrations  # noqa: E402
from ai_text_eval.dataset import load_demo_corpus  # noqa: E402
from ai_text_eval.detectors import available_detectors  # noqa: E402
from ai_text_eval.detectors.supervised import SupervisedDetector  # noqa: E402
from ai_text_eval.engine import DetectionEngine  # noqa: E402
from ai_text_eval.normalize import normalize  # noqa: E402
from ai_text_eval.text_features import words  # noqa: E402
from ai_text_eval.verdict import MIN_VERDICT_WORDS  # noqa: E402

WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"
MAX_BODY_BYTES = 1_000_000  # ~1 MB of text is far more than any real document

# Served inline so the app needs no binary assets and no external requests.
FAVICON_SVG = (
    b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'>"
    b"<rect width='512' height='512' rx='96' fill='#3b5bdb'/>"
    b"<path d='M128 336V176h40l88 120V176h40v160h-40l-88-120v120z' fill='#fff'/>"
    b"</svg>"
)


class Analyzer:
    """Holds the engine so models and training happen once, not per request."""

    def __init__(self, fast: bool = False, calibration_file: str | None = None):
        self.corroborators = available_detectors(include_model_based=not fast)

        corpus = load_demo_corpus()
        self.primary = SupervisedDetector(corroborators=self.corroborators)
        self.primary.fit([c.text for c in corpus], [c.label for c in corpus])
        self.trained_on_demo = True

        self._human_texts = [c.text for c in corpus if c.label == 0]
        self._calibration_file = calibration_file
        self._cache: dict[float, object] = {}

        base = DetectionEngine(corroborators=self.corroborators, primary=self.primary)
        self._human_scores = base.score_texts(self._human_texts)
        self._human_lengths = [len(words(t)) for t in self._human_texts]

    def engine_for(self, fpr_cap: float, language: str = "en") -> DetectionEngine:
        key = (fpr_cap, language)
        if key not in self._cache:
            if self._calibration_file:
                calibrations = load_calibrations(self._calibration_file)
            else:
                calibrations = {
                    language: calibrate(
                        self._human_scores, self._human_lengths,
                        alpha=fpr_cap, language=language,
                    )
                }
            self._cache[key] = DetectionEngine(
                corroborators=self.corroborators,
                primary=self.primary,
                calibrations=calibrations,
            )
        return self._cache[key]

    def analyze(self, text: str, fpr_cap: float, language: str,
                want_robustness: bool) -> dict:
        engine = self.engine_for(fpr_cap, language)
        result = engine.analyze(text, language=language)
        payload = result.to_dict(include_spans=True)

        calibration = engine.calibrations.get(language)
        payload["calibration"] = calibration.to_dict() if calibration else None
        payload["evidence"] = self._evidence(text)
        payload["config"] = {
            "fpr_cap": fpr_cap,
            "language": language,
            "min_verdict_words": MIN_VERDICT_WORDS,
            "trained_on_demo": self.trained_on_demo,
            "detectors": sorted(self.corroborators),
        }
        if want_robustness:
            payload["robustness"] = self._robustness(engine, text)
        return payload

    def _evidence(self, text: str) -> dict:
        clean, _ = normalize(text)
        out: dict = {}
        if "phrases" in self.corroborators:
            d = self.corroborators["phrases"].score(clean).details
            out["phrases"] = {
                "rate": d.get("weighted_rate_per_kw", 0.0),
                "hits": d.get("lexicon_hits", {}),
                "structural": d.get("structural_hits", {}),
                "triads": d.get("triads", 0),
            }
        if "stylometry" in self.corroborators:
            d = self.corroborators["stylometry"].score(clean).details
            out["stylometry"] = {
                k: d.get(k)
                for k in ("burstiness", "mattr", "mean_word_len",
                          "short_sentence_rate", "em_dash_per_kw")
            }
        return out

    def _robustness(self, engine: DetectionEngine, text: str) -> dict:
        clean, _ = normalize(text)
        baseline = engine.score_texts([clean])[0]
        rows = []
        for name in attack_names():
            attacked = apply_attack(name, clean, seed=0)
            corr = {n: d.score(attacked).score for n, d in engine.corroborators.items()}
            primary = None
            if engine.primary is not None:
                pr = engine.primary.score(attacked)
                if not pr.details.get("error"):
                    primary = pr.score
            rows.append({
                "attack": name,
                "undefended": round(engine._blend(primary, corr), 4),
                "defended": round(engine.score_texts([attacked])[0], 4),
            })
        return {"baseline": round(baseline, 4), "attacks": rows}


class Handler(BaseHTTPRequestHandler):
    analyzer: Analyzer = None  # set in serve()
    server_version = "ai-text-eval"
    sys_version = ""

    def log_message(self, fmt, *args):  # quieter console
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write(f"  {args[0]}\n")

    # -- helpers ---------------------------------------------------------

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # This app never loads anything remote; say so to the browser.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def _static(self, name: str, content_type: str) -> None:
        path = WEBAPP_DIR / name
        if not path.is_file():
            self._json(404, {"error": f"{name} not found"})
            return
        self._send(200, path.read_bytes(), content_type)

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            self._static("index.html", "text/html; charset=utf-8")
        elif route == "/manifest.webmanifest":
            self._static("manifest.webmanifest", "application/manifest+json")
        elif route == "/favicon.ico":
            self._send(200, FAVICON_SVG, "image/svg+xml")
        elif route == "/api/config":
            self._json(200, {
                "detectors": sorted(self.analyzer.corroborators),
                "min_verdict_words": MIN_VERDICT_WORDS,
                "attacks": attack_names(),
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/api/analyze":
            self._json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"error": "bad Content-Length"})
            return
        if length <= 0:
            self._json(400, {"error": "empty request"})
            return
        if length > MAX_BODY_BYTES:
            self._json(413, {"error": "text too large"})
            return

        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            self._json(400, {"error": f"invalid JSON: {err}"})
            return

        text = body.get("text", "")
        if not isinstance(text, str) or not text.strip():
            self._json(400, {"error": "no text supplied"})
            return

        try:
            fpr_cap = float(body.get("fpr_cap", 0.05))
        except (TypeError, ValueError):
            self._json(400, {"error": "fpr_cap must be a number"})
            return
        if not 0.0 < fpr_cap < 1.0:
            self._json(400, {"error": "fpr_cap must be between 0 and 1"})
            return

        try:
            payload = self.analyzer.analyze(
                text=text,
                fpr_cap=fpr_cap,
                language=str(body.get("language", "en")),
                want_robustness=bool(body.get("robustness", True)),
            )
        except Exception as err:  # a bad input should not kill the server
            self._json(500, {"error": f"{type(err).__name__}: {err}"})
            return
        self._json(200, payload)


def serve(host: str, port: int, fast: bool, calibration: str | None,
          open_browser: bool) -> int:
    print("Loading detectors and training the supervised layer…")
    Handler.analyzer = Analyzer(fast=fast, calibration_file=calibration)

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as err:
        print(f"error: cannot bind {host}:{port} — {err}", file=sys.stderr)
        print("try a different --port", file=sys.stderr)
        return 1

    url = f"http://{host}:{port}"
    print(f"\n  ai-text-eval  →  {url}\n")
    print("  Nothing you paste leaves this machine. Ctrl-C to stop.\n")
    if host not in ("127.0.0.1", "localhost"):
        print(
            f"  WARNING: bound to {host}, not just localhost. This server has no\n"
            "  authentication — anyone who can reach this port can use it.\n"
        )

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="serve.py", description="Local web app for ai-text-eval."
    )
    p.add_argument("--host", default="127.0.0.1",
                   help="interface to bind (default 127.0.0.1, localhost only)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--fast", action="store_true",
                   help="skip model-based detectors even if torch is installed")
    p.add_argument("--calibration", help="JSON calibration file to use instead of the demo corpus")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = p.parse_args(argv)
    return serve(args.host, args.port, args.fast, args.calibration, not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())

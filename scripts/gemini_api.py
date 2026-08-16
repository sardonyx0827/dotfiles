#!/usr/bin/env python3
"""Gemini REST client CLI — shared by the Vim/Neovim AI integration.

The editors' AI features used to shell out to the `gemini` CLI
(`cat payload | gemini -m MODEL -p INSTRUCTION`). This replaces that hop with a
direct call to the Gemini REST API — the same endpoint the bash-review hooks
(.claude/hooks/_bash_review_common.py) and the gemini-consultant MCP server
already talk to, so the repository has one answer to "how do we reach Gemini"
instead of three.

The shape deliberately mirrors scripts/secret_scan.py, the other helper both
editors invoke: a tiny numeric exit contract, the payload on stdin, and nothing
sensitive on stdout. Keeping the transport here rather than porting it into Lua
*and* VimScript is the whole point — the retry policy, the response parsing and
the truncation guard exist once and are unit-tested once, instead of drifting
apart in two independent editor ports the way the failure-message formatting
already did.

Contract (kept small on purpose so editor glue stays trivial):

    argv    --system <instruction>  required; the task description
            --model <id>            optional; see "model resolution" below
            --timeout <seconds>     optional; per-attempt request timeout
    stdin   the payload (a selection, a whole buffer, a diff) — never argv
    stdout  on success, the reply text and nothing else
    stderr  on failure, a single line saying why

    exit 0  -> a usable reply is on stdout
    exit 1  -> no usable reply: HTTP error, transport failure, an unparseable
               body, or a response the model did not finish (see extract_text)
    exit 2  -> GEMINI_API_KEY is not set. Split out from 1 because it is the
               one failure the reader can act on directly, and because a
               GUI-launched editor that never inherited the shell's environment
               lands on exactly this rather than on a network error.
               argparse also exits 2 on a usage error (a missing --system),
               which is its convention and not worth fighting; the two are told
               apart by what lands on stderr, which the editors show either way.

Note that exit 2 means the OPPOSITE of secret_scan.py's exit 2, even though the
two contracts are otherwise deliberately alike. There, 2 is "the scan did not
happen" and callers fail OPEN, because refusing every AI action when python is
missing would be worse than the risk. Here there is nothing to fail open to --
falling back to the `gemini` CLI is exactly what this replaced -- so 2 is a hard
stop that names the variable to export.

Model resolution: `--model`, else $GEMINI_MODEL, else DEFAULT_MODEL. Same
variable and same default as the bash-review hooks.

The API key is read from the environment only. It is never accepted on argv and
never written to disk: the editors spawn this process, and the child picks the
key out of its own environment, so it cannot show up in `ps aux` the way the
payload does for the one tool that has no stdin path (copilot). Nothing about
the request is logged either — unlike the gemini-consultant MCP server, which
records whole prompts under ~/.claude/logs. A consultation question is a
sentence the user wrote; these payloads are whatever happens to be in the
buffer, which is not the same thing at all.
"""

import argparse
import contextlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# Same variable and same default as .claude/hooks/bash-review.py (and its
# .codex twin), so "which Gemini model does this repo use" has a single answer.
# The Neovim side repeats the literal in ai/backend.lua because it also has to
# DISPLAY the model in a report header; tests/test_gemini_api_cli.py pins the
# two copies together so they cannot drift.
DEFAULT_MODEL = "gemini-flash-lite-latest"

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

# The model id is interpolated into the request PATH. Everything Google ships
# matches this, and refusing anything else keeps a stray `../` in $GEMINI_MODEL
# from steering the request at a different endpoint.
MODEL_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")

# Bounded by what an editor can sit through, not by what the model might want.
# The callers run this as an async job the user can cancel, but a wedged request
# still holds a diff tab open with nothing in it. Worst case is every attempt
# timing out: MAX_ATTEMPTS * DEFAULT_TIMEOUT plus the backoff between them, so
# 60 x 3 + (1 + 2) = 183s. A request that is merely SLOW is not retried -- only
# transport failures and RETRYABLE_STATUS are -- so the common case is one wait.
DEFAULT_TIMEOUT = 60.0
MAX_ATTEMPTS = 3

# Retried with backoff, because the same request may still succeed. Everything
# else (400 malformed, 403 bad key, 404 unknown model) fails identically every
# time, so retrying it only delays the message the reader needs.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

# How much of an upstream error message to quote. Long enough for Google's
# "model X is not found for API version v1beta" to arrive intact, short enough
# that a wall of JSON cannot push it out of the editor's report.
MAX_DETAIL = 400


class GeminiError(Exception):
    """A request that produced no usable reply; the message reaches stderr."""


def _emit(stream: Any, text: str) -> None:
    """Write UTF-8 bytes instead of going through the stream's text layer.

    sys.stdout's encoding follows the ambient locale, and these replies are
    routinely non-ASCII: ai/prompt.lua's buffer-check and hint prompts both say
    "Reply in Japanese". Under an encoding that cannot carry it -- measured with
    PYTHONIOENCODING=ascii, and reachable through a C locale on any system
    without C.UTF-8 for PEP 538 to coerce to -- the text layer raises
    UnicodeEncodeError, so the reply this call already paid for is lost and the
    editor gets a traceback instead.

    Same class of trap scripts/secret_scan.py documents on the READ side, where
    letting the locale decide made the exit code depend on the environment
    rather than on the payload. Encode explicitly at both ends.
    """
    stream.buffer.write(text.encode("utf-8"))
    stream.buffer.flush()


def _fail(message: str) -> int:
    """Report one line on stderr and answer with the generic failure code.

    The report itself is allowed to fail. When the editor cancels a job (`q` on
    the diff tab) both pipes can already be closed, and raising out of here
    would replace a clean non-zero exit with a traceback about the complaint
    rather than about the problem.
    """
    with contextlib.suppress(OSError):
        _emit(sys.stderr, message + "\n")
    return 1


def _resolve_model(explicit: str | None) -> str:
    """`--model`, else $GEMINI_MODEL, else DEFAULT_MODEL.

    Whitespace-only values count as unset rather than as a model id: an
    exported-but-empty GEMINI_MODEL is a common shell accident, and letting it
    through would turn into a 404 that reads as though the API were broken.
    """
    model = ""
    for candidate in (explicit, os.environ.get("GEMINI_MODEL")):
        if candidate and candidate.strip():
            model = candidate.strip()
            break
    model = model or DEFAULT_MODEL
    if not MODEL_RE.match(model):
        raise GeminiError(f"invalid model id: {model!r}")
    return model


def build_request(
    model: str, system: str, payload: str, api_key: str
) -> urllib.request.Request:
    """Build one generateContent POST.

    `systemInstruction` carries the task and `contents` carries the text being
    worked on, which is the same split the CLI had (`-p INSTRUCTION` alongside
    stdin) — so the prompts in ai/prompt.lua keep meaning what they meant.

    No `generationConfig` is sent, and both omissions are deliberate:

    * No `maxOutputTokens`. The model's own ceiling is the right bound when the
      caller may be rewriting an entire buffer; a hand-picked number smaller
      than that only manufactures the MAX_TOKENS failure extract_text has to
      refuse.
    * No `thinkingConfig`. `thinkingLevel: "minimal"` — which
      .claude/hooks/bash-review.py does send — was measured to be accepted only
      by gemini-flash-lite-latest; gemini-flash-latest, gemini-pro-latest and
      gemini-2.5-flash-lite all answer HTTP 400 "Thinking level ... is not
      supported for this model". $GEMINI_MODEL is user-overridable, so sending
      it would make an override of the default silently break the feature. The
      hooks get away with it because they pin their own model and fall back.
    """
    body = json.dumps(
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": payload}]}],
        }
    ).encode("utf-8")
    return urllib.request.Request(
        f"{API_ROOT}/{model}:generateContent",
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )


def _error_detail(raw: str) -> str:
    """Pull `error.message` out of an error body, falling back to the body."""
    try:
        decoded = json.loads(raw)
    except ValueError:
        return raw.strip()[:MAX_DETAIL]
    if isinstance(decoded, dict):
        err = decoded.get("error")
        if isinstance(err, dict):
            message = err.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:MAX_DETAIL]
    return raw.strip()[:MAX_DETAIL]


def extract_text(body: Any) -> str:
    """Return the reply text, or raise GeminiError explaining why there is none.

    The failure that matters most here arrives as HTTP 200. When the answer
    runs into the output limit the API still returns success, with
    `finishReason: "MAX_TOKENS"` and TRUNCATED text — and the callers splice
    this reply over a selection or a whole buffer, so handing that back would
    apply a reply written for a fragment to the entire range. That is the same
    harm run_cli's ARG_MAX guard refuses in the outbound direction, so refuse
    it here too rather than returning a partial answer that looks complete.
    """
    if not isinstance(body, dict):
        raise GeminiError("unexpected response shape")

    err = body.get("error")
    if isinstance(err, dict) and err.get("message"):
        raise GeminiError(str(err["message"])[:MAX_DETAIL])

    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        feedback = body.get("promptFeedback")
        blocked = feedback.get("blockReason") if isinstance(feedback, dict) else None
        if blocked:
            raise GeminiError(f"request blocked (blockReason={blocked})")
        raise GeminiError("no candidates returned")

    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise GeminiError("unexpected response shape")

    reason = str(candidate.get("finishReason") or "")
    if reason == "MAX_TOKENS":
        raise GeminiError(
            "response truncated at the model's output limit "
            "(finishReason=MAX_TOKENS); retry over a smaller range"
        )
    if reason not in ("", "STOP"):
        raise GeminiError(f"response was not completed (finishReason={reason})")

    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    text = ""
    if isinstance(parts, list):
        text = "".join(
            part["text"]
            for part in parts
            # A part flagged `thought` is the model's reasoning, not the answer;
            # splicing it into the user's buffer would be worse than nothing.
            if isinstance(part, dict)
            and isinstance(part.get("text"), str)
            and not part.get("thought")
        )
    if not text.strip():
        raise GeminiError("empty response")
    return text


def request_generate(
    model: str,
    system: str,
    payload: str,
    api_key: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = MAX_ATTEMPTS,
    sleep: Any = time.sleep,
) -> Any:
    """POST with bounded retries and return the decoded response body.

    The Request is rebuilt each attempt because urllib consumes it (the same
    note the gemini-consultant MCP server carries). `sleep` is injectable so
    the retry tests do not spend the backoff in real time.
    """
    last = ""
    for attempt in range(attempts):
        req = build_request(model, system, payload, api_key)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec: B310
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc.read().decode("utf-8", errors="replace"))
            last = f"HTTP {exc.code}: {detail}" if detail else f"HTTP {exc.code}"
            # A 4xx that is not in RETRYABLE_STATUS will fail the same way three
            # times; report it now so the reader sees the real cause instead of
            # waiting out a backoff for nothing.
            if exc.code not in RETRYABLE_STATUS:
                raise GeminiError(last) from exc
        except OSError as exc:
            # URLError and TimeoutError are both OSError subclasses, and so is a
            # connection reset raised while reading the body; one clause covers
            # every transport failure worth retrying.
            last = f"{type(exc).__name__}: {exc}"
        except ValueError as exc:
            # A body that is not JSON is not a transport hiccup — retrying it
            # just asks the same broken endpoint the same question again.
            raise GeminiError(f"invalid JSON response: {exc}") from exc
        if attempt < attempts - 1:
            sleep(2**attempt)
    raise GeminiError(last or "request failed")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a payload on stdin to the Gemini REST API."
    )
    parser.add_argument(
        "--system", required=True, help="the instruction describing the task"
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"model id (default: ${{GEMINI_MODEL}} or {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-attempt timeout"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Checked before stdin is touched: this is the one failure that does not
    # depend on the payload, and reporting it costs nothing.
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        _emit(sys.stderr, "GEMINI_API_KEY is not set; export it (see ~/.zsh_secrets)\n")
        return 2

    try:
        model = _resolve_model(args.model)
        # Decoded explicitly rather than through sys.stdin's text layer, whose
        # error handler follows the ambient locale — the same trap
        # scripts/secret_scan.py documents at length. Undecodable bytes become
        # replacement characters and are still sent, because the alternative is
        # refusing to answer a question about a buffer that is mostly text.
        payload = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        if not payload.strip():
            # The API answers an empty part with a 400; saying so here is both
            # faster and comprehensible.
            raise GeminiError("empty payload on stdin")
        body = request_generate(
            model, args.system, payload, api_key, timeout=args.timeout
        )
        text = extract_text(body)
        _emit(sys.stdout, text if text.endswith("\n") else text + "\n")
    except GeminiError as exc:
        # Google's error.message can itself be non-ASCII, so stderr goes through
        # the same explicit encoding as the reply does.
        return _fail(str(exc))
    except OSError as exc:
        # Reading stdin, or writing the reply into a pipe the editor has already
        # closed because the user pressed `q` on the diff tab. The exception text
        # says which; the caller could not act differently either way.
        return _fail(f"i/o error: {exc}")
    except Exception as exc:  # noqa: BLE001 - the contract is one line, always
        # The editors render whatever lands on stderr straight into a report
        # window, so an unforeseen exception must not arrive there as a
        # traceback. Same call scripts/secret_scan.py makes for its own scanner.
        return _fail(f"unexpected failure: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

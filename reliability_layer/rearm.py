"""Semantically guarded, minimal provider-confirmed rearm fix.

Only supports the upstream predicate observed on Hermes v0.22.2. Future releases
are supported when and ONLY when the same predicate and callable interface remain.
No destructive edits are made when the structure differs.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

MARKER = "HERMES_RELIABILITY_VERIFIED_HEADROOM_V1"
FUNCTION = "_should_rearm_compression_budget"
# Deliberately scoped to a supported local Ollama profile:
# context=65,536, compression threshold=40,960, reserve=16,384.
EXPECTED_THRESHOLD = 40960
MAX_REARM_PROMPT = 49152


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def locate(text: str):
    tree = ast.parse(text)
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == FUNCTION]
    if len(functions) != 1:
        raise ValueError("Missing or ambiguous upstream rearm helper")
    fn = functions[0]
    names = [arg.arg for arg in fn.args.args + fn.args.kwonlyargs]
    if names != ["compression_attempts", "completed_compaction_pending", "prompt_tokens", "threshold_tokens"]:
        raise ValueError("Upstream rearm signature has changed")
    if fn.args.vararg or fn.args.kwarg or fn.args.defaults or fn.args.kw_defaults != [None] * len(fn.args.kw_defaults):
        raise ValueError("Upstream rearm parameters changed")
    return fn


def upstream_shape(text: str) -> bool:
    fn = locate(text)
    if MARKER in ast.get_source_segment(text, fn):
        return False
    if len(fn.body) != 2 or not isinstance(fn.body[0], ast.Expr) or not isinstance(fn.body[0].value, ast.Constant) or not isinstance(fn.body[0].value.value, str):
        return False
    expr = fn.body[1]
    if not isinstance(expr, ast.Return):
        return False
    # Compare the semantic AST rather than an entire version-specific file digest.
    standard = ast.parse("return bool(compression_attempts and completed_compaction_pending and 0 < prompt_tokens < threshold_tokens)").body[0]
    return ast.dump(expr, include_attributes=False) == ast.dump(standard, include_attributes=False)


def classification(text: str) -> str:
    try:
        fn = locate(text)
    except (ValueError, SyntaxError):
        return "incompatible"
    if MARKER in ast.get_source_segment(text, fn):
        # Require a coherent patched function, not just the marker string.
        try:
            expected = patch(_make_upstream_fixture())
            installed_fn = locate(text)
            expected_fn = locate(expected)
            # A future upstream release may edit the docstring without changing
            # the recognized patched predicate. Compare the final 3 statements
            # and the already verified signature, not the whole function text.
            installed_tail = [ast.dump(n, include_attributes=False) for n in installed_fn.body[-3:]]
            expected_tail = [ast.dump(n, include_attributes=False) for n in expected_fn.body[-3:]]
            if installed_tail == expected_tail and len(installed_fn.body) == 4:
                return "installed"
        except Exception:
            pass
        return "incompatible"
    return "supported" if upstream_shape(text) else "incompatible"


def _make_upstream_fixture():
    # Used only to derive the patched-function AST for integrity verification.
    return '''def _should_rearm_compression_budget(\n    compression_attempts: int, *, completed_compaction_pending: bool, prompt_tokens: int, threshold_tokens: int\n) -> bool:\n    """True once a provider proves a completed compaction worked: rough estimates cannot\n    rearm the anti-thrash budget, only the completed-compaction latch plus a positive\n    normalized prompt count below the threshold."""\n    return bool(\n        compression_attempts and completed_compaction_pending and 0 < prompt_tokens < threshold_tokens\n    )\n'''


def patch(text: str) -> str:
    if not upstream_shape(text):
        raise ValueError("Upstream rearm semantics changed; refusing to patch")
    fn = locate(text)
    replacement = (
        "    # " + MARKER + ": rearm only after a completed compression AND a\n"
        "    # verified provider prompt. Leave every other configuration unchanged.\n"
        "    if not (compression_attempts and completed_compaction_pending and 0 < prompt_tokens):\n"
        "        return False\n"
        "    if prompt_tokens < threshold_tokens:\n"
        "        return True\n"
        "    return bool(threshold_tokens == 40960 and prompt_tokens <= 49152)\n"
    )
    original = fn.body[1]
    lines = text.splitlines(keepends=True)
    output = "".join(lines[:original.lineno - 1]) + replacement + "".join(lines[original.end_lineno:])
    compile(output, "<patched-conversation-loop>", "exec")
    return output


def function_under_test(text: str):
    """Compile JUST the function node for safe isolated unit tests, without imports."""
    import typing
    fn = locate(text)
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"Any": typing.Any}
    exec(compile(module, "<isolated-rearm>", "exec"), namespace)
    return namespace[FUNCTION]

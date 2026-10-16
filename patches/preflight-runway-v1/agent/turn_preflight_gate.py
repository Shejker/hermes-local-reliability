"""Pre-API pressure gate for the conversation turn loop: the Ollama runtime-context floor,
the provider-overflow re-check arming, the insufficient-progress blocker (compares fully
assembled requests, not raw ``messages``) and the call into
``turn_preflight.run_preflight_compression``. Nothing here imports
``agent.conversation_loop`` at module level (cycle)."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from agent.message_metadata import append_message
from agent.turn_context import _compression_warrants_another_preflight_pass
from agent.turn_preflight import PreflightGateVerdict, run_preflight_compression

logger = logging.getLogger("agent.conversation_loop")


def _local_ollama_runway_limit(agent: Any) -> int | None:
    """Conservative *pre-send* runway for an Ollama /v1 chat-completions backend.

    This is a fail-closed last resort, not an automatic compression retry. It does
    not assume the threshold reflects the actual request size; Ollama's provider
    token counts can differ from Hermes' rough post-compaction estimate.
    """
    if getattr(agent, "api_mode", "") != "chat_completions":
        return None
    try:
        u = urlsplit(str(getattr(agent, "base_url", "") or ""))
        if u.hostname not in {"127.0.0.1", "localhost", "::1"} or u.port != 11434:
            return None
        cap = int(getattr(getattr(agent, "context_compressor", None), "context_length", 0) or 0)
    except (ValueError, TypeError):
        return None
    if cap < 16384:
        return None
    reserve = min(8192, cap // 8)
    return cap - reserve


def run_preflight_gate(
    agent: Any, *, request_pressure_tokens: Any, _moa_prepared_request: Any,
    pending_moa_prepared_request: Any, messages: Any, system_message: Any, user_message: Any,
    active_system_prompt: Any, conversation_history: Any, api_call_count: Any,
    compression_attempts: Any, max_compression_attempts: Any, effective_task_id: Any,
    final_response: Any, failed: Any, _turn_exit_reason: Any, _compression_timeout_exhausted: Any,
    _preflight_compression_blocked: Any, _provider_overflow_recovery_pending: Any,
    _last_preflight_pressure: Any,
) -> PreflightGateVerdict:
    """Run the pre-API guard chain in the original order. ``_last_preflight_pressure`` is
    consumed here (set to None) and re-armed only by a compression pass, so a blocked
    preflight never compares against a stale figure."""
    from agent.conversation_loop import _ollama_context_limit_error

    v = PreflightGateVerdict(
        action="fallthrough", pending_moa_prepared_request=pending_moa_prepared_request,
        messages=messages, active_system_prompt=active_system_prompt,
        conversation_history=conversation_history, api_call_count=api_call_count,
        compression_attempts=compression_attempts, final_response=final_response, failed=failed,
        _turn_exit_reason=_turn_exit_reason,
        _compression_timeout_exhausted=_compression_timeout_exhausted,
        _preflight_compression_blocked=_preflight_compression_blocked,
        _provider_overflow_recovery_pending=_provider_overflow_recovery_pending,
        _last_preflight_pressure=None,
    )

    _runtime_context_error = _ollama_context_limit_error(agent, request_pressure_tokens)
    if _runtime_context_error:
        v.final_response = _runtime_context_error
        v.failed = True
        v._turn_exit_reason = "ollama_runtime_context_too_small"
        append_message(messages, {"role": "assistant", "content": v.final_response})
        agent._emit_diagnostic_status("❌ Ollama runtime context is too small for Hermes tool use")
        v.api_call_count -= 1
        agent._api_call_count = v.api_call_count
        with suppress(Exception):
            agent.iteration_budget.refund()
        v.action = "break"
        return v

    # Pre-API pressure check: tool results grow a turn and last_prompt_tokens lags
    # them. Mirror the turn-prologue guard chain: defer on noisy estimate, skip in
    # failure cooldown, then should_compress().
    _compressor = agent.context_compressor
    _preflight_threshold = int(getattr(_compressor, "threshold_tokens", 0) or 0)
    _provider_overflow_preflight = _provider_overflow_recovery_pending and (
        _preflight_threshold <= 0 or request_pressure_tokens >= _preflight_threshold
    )
    if _provider_overflow_recovery_pending and not _provider_overflow_preflight:
        # The outer-loop rebuild includes system prompt, request-only injections and
        # tool schemas; only that full request with output runway may be sent.
        v._provider_overflow_recovery_pending = False
    # Compare fully assembled requests, not raw ``messages`` (which omit
    # api_content, plugin injections, prefills, MoA context, ephemeral system text).
    if (
        _last_preflight_pressure is not None
        and request_pressure_tokens >= _preflight_threshold
        and not _compression_warrants_another_preflight_pass(
            _last_preflight_pressure, request_pressure_tokens, _preflight_threshold
        )
    ):
        # Stop proactive retries this turn without consuming the shared overflow-
        # recovery budget; the provider's error handler may still compact.
        v._preflight_compression_blocked = True
        logger.warning(
            "Pre-API compression made insufficient progress: ~%s -> "
            "~%s request tokens; skipping additional preflight passes",
            f"{_last_preflight_pressure:,}",
            f"{request_pressure_tokens:,}",
        )
    # Auditable, privacy-safe reason data for high-context calls. This records
    # whether the attempt budget, deferral, or cooldown prevented compaction;
    # it logs no message content or credentials.
    if isinstance(request_pressure_tokens, (int, float)) and request_pressure_tokens >= _preflight_threshold > 0:
        cooldown = getattr(_compressor, "get_active_compression_failure_cooldown", lambda: None)()
        logger.info(
            "Pre-API pressure audit: estimated=%d limit=%s threshold=%d "
            "attempts=%d/%d anchored=%s may_defer_unanchored=%s cooldown=%s "
            "blocked=%s provider_overflow=%s",
            int(request_pressure_tokens),
            getattr(_compressor, "context_length", "unknown"),
            _preflight_threshold, compression_attempts, max_compression_attempts,
            bool(getattr(agent, "_request_pressure_anchored", False)),
            bool(not getattr(agent, "_request_pressure_anchored", False)),
            bool(cooldown), bool(v._preflight_compression_blocked),
            bool(_provider_overflow_preflight),
        )
    verdict = run_preflight_compression(
        agent, v, compressor=_compressor, request_pressure_tokens=request_pressure_tokens,
        provider_overflow_preflight=_provider_overflow_preflight,
        # An anchored figure is real usage + delta: never deferred. Only a whole-context rough
        # estimate waits for the provider's count.
        defer_preflight=(
            (lambda _t: False) if getattr(agent, "_request_pressure_anchored", False)
            else getattr(_compressor, "should_defer_preflight_to_real_usage", lambda _t: False)
        ),
        moa_prepared_request=_moa_prepared_request, system_message=system_message,
        user_message=user_message, max_compression_attempts=max_compression_attempts,
        effective_task_id=effective_task_id,
    )
    # A soft fail-closed boundary: *only* if normal preflight chose to send the
    # unchanged request. Never cancel a running compression, never mutate SQLite,
    # and never trigger extra retries after the anti-thrash budget is exhausted.
    runway_limit = _local_ollama_runway_limit(agent)
    if (
        verdict.action == "fallthrough"
        and runway_limit is not None
        and isinstance(request_pressure_tokens, (int, float))
        and not isinstance(request_pressure_tokens, bool)
        and request_pressure_tokens >= runway_limit
    ):
        cap = getattr(_compressor, "context_length", 0)
        logger.error(
            "Pre-API runway guard BLOCKED oversized Ollama request: "
            "estimated=%d safe_limit=%d context=%d attempts=%d/%d anchored=%s",
            int(request_pressure_tokens), runway_limit, cap,
            verdict.compression_attempts, max_compression_attempts,
            bool(getattr(agent, "_request_pressure_anchored", False)),
        )
        verdict.final_response = (
            "Hermes paused this turn before sending an overfull prompt to local Ollama "
            f"(estimated {int(request_pressure_tokens):,} tokens; safe pre-send limit "
            f"{runway_limit:,} of {cap:,}). Automatic compression was unavailable or "
            "did not free enough space. Run /compress, check /context, then continue "
            "from the last confirmed project state. No tool execution was retried."
        )
        verdict.failed = True
        verdict._turn_exit_reason = "context_runway_guard"
        append_message(verdict.messages, {"role": "assistant", "content": verdict.final_response})
        diagnostic = getattr(agent, "_emit_diagnostic_status", None)
        if callable(diagnostic):
            diagnostic("Context runway guard paused oversized Ollama request; /compress required")
        # Mirror the existing _ollama_context_limit_error branch: no provider call
        # happened in this iteration, so release its provisional API-call budget.
        verdict.api_call_count -= 1
        agent._api_call_count = verdict.api_call_count
        with suppress(Exception):
            agent.iteration_budget.refund()
        verdict.action = "break"
    return verdict

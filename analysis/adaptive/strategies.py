"""Retraining strategies for the Phase 7 adaptive orchestrator.

Each strategy answers exactly one question on every replay step::

    should_retrain(t_now_s, drift_signals) -> (bool, reason)

Strategies are deliberately stateful (so `Periodic` can remember when it
last fired) but state is reset by :meth:`reset` so the same strategy
instance can be reused across orchestrator runs in tests.

Defensible defaults
-------------------
The three classes implement the three baselines required by
``docs/plan.md`` Phase 7. Hyperparameters are chosen to keep the
comparison fair:

- ``StaticStrategy`` never retrains after the initial fit — this is the
  "do nothing" baseline. Its cost ledger has exactly 1 entry
  (the warm-up fit).

- ``PeriodicStrategy`` retrains every ``period_s`` seconds of timeline
  time, on a clock that *starts* counting from the orchestrator's
  warm-up completion (not t=0). 180 s is the default because Phase 6's
  ``timeline_medium`` averages a drift event roughly every 225 s
  (8 drifts / 1 800 s), so 180 s gives the periodic baseline a fighting
  chance of retraining *just before* a drift hits.

- ``DriftTriggeredStrategy`` retrains whenever ANY watched drift
  detector reports ``True`` AND the cooldown has elapsed. The
  ``cooldown_s`` exists because river detectors (e.g. ADWIN) can fire
  several times in quick succession around the same change point —
  without cooldown the orchestrator would refit on near-identical
  windows, inflating the cost ledger without benefit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class RetrainStrategy(ABC):
    """Abstract base. Subclasses implement :meth:`should_retrain`."""

    name: str = "base"

    @abstractmethod
    def should_retrain(
        self,
        t_now_s: float,
        drift_signals: Mapping[str, bool],
    ) -> tuple[bool, str]:
        """Decide whether to retrain at simulated time ``t_now_s``.

        Args:
            t_now_s       : current timeline time, seconds.
            drift_signals : mapping ``stream_name -> True`` for streams
                            whose drift detector fired since the previous
                            decision step. Strategies that ignore drift
                            (Static, Periodic) may discard this argument.

        Returns:
            ``(refit, reason)``. ``refit=True`` triggers a refit;
            ``reason`` is a short tag stored in the cost-ledger row
            (e.g. ``"warmup"``, ``"periodic"``, ``"drift:hosr_rolling"``).
            When ``refit=False`` the reason is unused but should still
            be a stable, non-empty string for logging.
        """

    def reset(self) -> None:
        """Reset stateful book-keeping. Default no-op."""


# ---------------------------------------------------------------------------
# Static — never retrain
# ---------------------------------------------------------------------------

@dataclass
class StaticStrategy(RetrainStrategy):
    """Fit once at warm-up; never retrain afterwards.

    Operationally: the "do nothing" baseline that any adaptive system
    must beat under drift. Cost ledger: 1 entry (the warm-up fit).
    """

    name: str = "static"

    def should_retrain(
        self,
        t_now_s: float,
        drift_signals: Mapping[str, bool],
    ) -> tuple[bool, str]:
        return False, "static_skip"


# ---------------------------------------------------------------------------
# Periodic — refit every K seconds
# ---------------------------------------------------------------------------

@dataclass
class PeriodicStrategy(RetrainStrategy):
    """Refit every ``period_s`` seconds of timeline time.

    The clock starts at the time of the first :meth:`should_retrain`
    call (which the orchestrator invokes once the warm-up fit has
    completed). After that, the next refit is scheduled at
    ``_last_refit_t_s + period_s``.
    """

    period_s: float = 180.0
    name: str = "periodic"
    _last_refit_t_s: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.period_s <= 0:
            raise ValueError(f"period_s must be > 0 (got {self.period_s})")

    def reset(self) -> None:
        self._last_refit_t_s = None

    def should_retrain(
        self,
        t_now_s: float,
        drift_signals: Mapping[str, bool],
    ) -> tuple[bool, str]:
        if self._last_refit_t_s is None:
            # First decision step after warm-up: start the clock and
            # skip — the warm-up fit just happened, no need to retrain.
            self._last_refit_t_s = float(t_now_s)
            return False, "periodic_init"
        if (t_now_s - self._last_refit_t_s) >= self.period_s:
            self._last_refit_t_s = float(t_now_s)
            return True, f"periodic:{self.period_s:g}s"
        return False, "periodic_wait"


# ---------------------------------------------------------------------------
# Drift-triggered — refit on drift signal (with cooldown)
# ---------------------------------------------------------------------------

@dataclass
class DriftTriggeredStrategy(RetrainStrategy):
    """Refit when any watched drift detector fires (subject to cooldown).

    Args:
        cooldown_s    : minimum seconds between consecutive refits. Set
                        to 0 to fire on every drift signal (not
                        recommended; river ADWIN can fire 5-10 times
                        around a single change point).
        watch_streams : optional explicit list of stream names to
                        consider. ``None`` means "watch all keys in
                        ``drift_signals``". When supplied, only signals
                        from these streams trigger refits — useful in
                        tests where the orchestrator feeds a richer
                        signals dict than the strategy cares about.
    """

    cooldown_s: float = 30.0
    watch_streams: list[str] | None = None
    name: str = "drift_triggered"
    _last_refit_t_s: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.cooldown_s < 0:
            raise ValueError(f"cooldown_s must be >= 0 (got {self.cooldown_s})")

    def reset(self) -> None:
        self._last_refit_t_s = None

    def should_retrain(
        self,
        t_now_s: float,
        drift_signals: Mapping[str, bool],
    ) -> tuple[bool, str]:
        fired = [
            s for s, hit in drift_signals.items()
            if hit and (self.watch_streams is None or s in self.watch_streams)
        ]
        if not fired:
            return False, "no_signal"
        if self._last_refit_t_s is not None:
            elapsed = t_now_s - self._last_refit_t_s
            if elapsed < self.cooldown_s:
                return False, f"cooldown:{elapsed:.0f}s<{self.cooldown_s:g}s"
        self._last_refit_t_s = float(t_now_s)
        return True, f"drift:{','.join(sorted(fired))}"


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------

def default_strategies(
    period_s: float = 180.0,
    cooldown_s: float = 30.0,
    watch_streams: list[str] | None = None,
) -> list[RetrainStrategy]:
    """Return the three Phase 7 Iter A baseline strategies.

    Order matches the convention used in the headline figure (left to
    right): static, periodic, drift-triggered.
    """
    return [
        StaticStrategy(),
        PeriodicStrategy(period_s=period_s),
        DriftTriggeredStrategy(cooldown_s=cooldown_s, watch_streams=watch_streams),
    ]

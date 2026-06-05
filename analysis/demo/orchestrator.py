"""DemoOrchestrator: walk-forward replay with surrogate-driven interventions.

The Phase 9 Iter A orchestrator is a single-strategy specialisation of
the Phase 7 :class:`AdaptiveOrchestrator`: it always uses the
**drift-triggered-filtered** retrain strategy (Phase 7 winner), but
extends the drift-trigger callback with a Phase 8 surrogate query.

Pipeline per drift trigger:

    1. Compute the deployment context (RSRP/SINR/RSRQ percentiles over
       the last `context_window_s` seconds of samples).
    2. Look up the current (TTT, hyst, A3) from `events.parquet`.
    3. Query the Phase 8 surrogate via :func:`inverse_query` for the
       feasible recommendation that maximises HOSR.
    4. Compute the counterfactual KPI uplift:
            uplift_HOSR = surrogate.predict(recommended) - surrogate.predict(current)
       under the *same* deployment context, for every target KPI.
    5. Log a :class:`Intervention` record.

The score loop, retrain bookkeeping, and CPU-cost ledger reuse the
Phase 7 :class:`AdaptiveOrchestrator` verbatim; the surrogate query
runs once per drift event (cheap) without altering anomaly scoring.

ADR-15 enforcement: every reported KPI is mobility-only (HOSR, RLF_rate,
ping_pong_rate). No throughput in any uplift computation.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Mapping, Protocol

import numpy as np
import pandas as pd

from analysis.adaptive.orchestrator import (
    AdaptiveOrchestrator,
    OrchestratorConfig,
    RunResult,
)
from analysis.adaptive.strategies import DriftTriggeredStrategy
from analysis.config_perf.data import SCENARIO_COLS, TARGET_COLS
from analysis.config_perf.inverse import (
    Constraint,
    InverseQueryResult,
    inverse_query,
)
from analysis.demo.context import (
    DeploymentContext,
    build_context,
    current_config,
)


# ---------------------------------------------------------------------------
# Intervention record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Intervention:
    """One surrogate-driven config recommendation triggered by drift.

    Attributes preserve the full counterfactual chain so the moneyshot
    figure + thesis table can be reconstructed from `intervention_log.csv`
    without re-running the demo.
    """

    t_trigger_s: float
    drift_streams_fired: tuple[str, ...]
    context: DeploymentContext

    current_cfg: dict[str, float]            # {"ttt_ms": ..., "hyst_db": ..., "a3_off_db": ...}
    recommended_cfg: dict[str, float]

    pred_current: dict[str, float]           # {target -> predicted KPI under current cfg}
    pred_recommended: dict[str, float]       # {target -> predicted KPI under recommended cfg}
    pred_recommended_lo: dict[str, float]    # conformal 90 % PI lower
    pred_recommended_hi: dict[str, float]    # conformal 90 % PI upper
    n_feasible: int = 0
    n_candidates: int = 0
    cfg_changed: bool = True

    def uplift(self, target: str) -> float:
        """Predicted uplift = recommended − current (positive = improvement).

        For HOSR (higher = better) this is the "raw" delta. For
        RLF_rate / ping_pong_rate (lower = better), the caller should
        flip the sign when reporting. :func:`signed_uplift` does this
        automatically.
        """
        return float(self.pred_recommended[target] - self.pred_current[target])

    def signed_uplift(self, target: str) -> float:
        """Sign-corrected uplift: positive = improvement on the human scale.

        HOSR is higher-is-better, so uplift = +delta.
        RLF_rate / ping_pong_rate are lower-is-better, so signed = -delta.
        Convention used in the cumulative-uplift figure panel.
        """
        raw = self.uplift(target)
        return raw if target == "hosr" else -raw

    def as_record(self) -> dict:
        """Flatten to a single dict suitable for `pd.DataFrame.from_records`."""
        out: dict = {
            "t_trigger_s": self.t_trigger_s,
            "drift_streams_fired": ",".join(self.drift_streams_fired),
            "n_feasible": self.n_feasible,
            "n_candidates": self.n_candidates,
            "cfg_changed": self.cfg_changed,
            "context_n_samples": self.context.n_samples,
        }
        for k in ("ttt_ms", "hyst_db", "a3_off_db"):
            out[f"current_{k}"] = self.current_cfg.get(k, float("nan"))
            out[f"recommended_{k}"] = self.recommended_cfg.get(k, float("nan"))
        for t in TARGET_COLS:
            out[f"pred_current_{t}"] = self.pred_current.get(t, float("nan"))
            out[f"pred_recommended_{t}"] = self.pred_recommended.get(t, float("nan"))
            out[f"pred_recommended_{t}_lo"] = self.pred_recommended_lo.get(t, float("nan"))
            out[f"pred_recommended_{t}_hi"] = self.pred_recommended_hi.get(t, float("nan"))
            out[f"signed_uplift_{t}"] = self.signed_uplift(t)
        # Echo the 9 scenario percentiles for full provenance
        out.update(self.context.to_surrogate_dict())
        return out


# ---------------------------------------------------------------------------
# Surrogate bundle (point + conformal interval models per target)
# ---------------------------------------------------------------------------

class SurrogateLike(Protocol):
    def predict(self, X) -> np.ndarray: ...


class QuantileSurrogateLike(Protocol):
    def predict(self, X) -> np.ndarray: ...
    def predict_interval(self, X) -> tuple[np.ndarray, np.ndarray, np.ndarray]: ...


@dataclass
class SurrogateBundle:
    """Pre-fitted point + interval surrogates, one per target KPI."""

    point_models: dict[str, SurrogateLike]          # target -> HistGB-style model
    interval_models: dict[str, QuantileSurrogateLike]  # target -> ConformalQuantileGB-style model

    def validate(self) -> None:
        for t in TARGET_COLS:
            if t not in self.point_models:
                raise ValueError(f"missing point surrogate for target {t!r}")
            if t not in self.interval_models:
                raise ValueError(f"missing interval surrogate for target {t!r}")


# ---------------------------------------------------------------------------
# Config + Result
# ---------------------------------------------------------------------------

@dataclass
class DemoConfig:
    """Knobs for the Phase 9 demo run.

    Most defaults mirror Phase 7 Iter A (so the demo replays exactly
    the same anomaly-scoring + retrain decisions as the
    `drift_triggered_filtered` benchmark variant).
    """

    feature_cols: list[str] = field(default_factory=list)
    warmup_max_phase_id: int = 3
    retrain_window_s: float = 120.0
    filter_quantile: float = 0.8         # Phase 7 winner
    drift_cooldown_s: float = 30.0
    watch_streams: tuple[str, ...] = ("hosr_rolling", "rlf_rate_rolling")
    context_window_s: float = 60.0       # 1 phase length
    constraint_hosr: float = 0.95
    constraint_rlf: float = 0.05
    constraint_pp: float = 0.10
    score_chunk_size: int = 4096
    random_state: int = 42

    def __post_init__(self) -> None:
        if not self.feature_cols:
            raise ValueError("feature_cols must be non-empty")
        if self.context_window_s <= 0:
            raise ValueError(f"context_window_s must be > 0 (got {self.context_window_s})")
        if not (0.0 < self.filter_quantile <= 1.0):
            raise ValueError(
                f"filter_quantile must be in (0, 1] (got {self.filter_quantile})"
            )
        if not self.watch_streams:
            raise ValueError("watch_streams must be non-empty")


@dataclass
class DemoResult:
    per_window: pd.DataFrame
    retrain_log: pd.DataFrame
    interventions: list[Intervention]
    base_detector_name: str

    def intervention_log_df(self) -> pd.DataFrame:
        """Flatten interventions to a DataFrame.

        Empty case still returns a DataFrame with the full canonical
        schema (so downstream CSV readers / dashboards never break on
        "no drift detected" runs). The schema mirrors what
        :meth:`Intervention.as_record` would produce on a non-empty
        intervention — keep them in sync.
        """
        if not self.interventions:
            cols = ["t_trigger_s", "drift_streams_fired",
                    "n_feasible", "n_candidates", "cfg_changed",
                    "context_n_samples"]
            for k in ("ttt_ms", "hyst_db", "a3_off_db"):
                cols.append(f"current_{k}")
                cols.append(f"recommended_{k}")
            for t in TARGET_COLS:
                cols.append(f"pred_current_{t}")
                cols.append(f"pred_recommended_{t}")
                cols.append(f"pred_recommended_{t}_lo")
                cols.append(f"pred_recommended_{t}_hi")
                cols.append(f"signed_uplift_{t}")
            cols.extend(SCENARIO_COLS)
            return pd.DataFrame(columns=cols)
        return pd.DataFrame.from_records([iv.as_record() for iv in self.interventions])


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class DemoOrchestrator:
    """Walk-forward demo runner.

    Internally delegates the entire anomaly-score + retrain loop to a
    Phase 7 :class:`AdaptiveOrchestrator` configured with the
    drift-triggered-filtered strategy. The Phase 9-specific extension
    is an *event-replay* post-pass over the Phase 7 `retrain_log` that
    queries the surrogate at each drift-triggered retrain event.

    Why post-pass instead of in-loop:
        Keeping the score loop bit-identical to Phase 7 means the
        demo's anomaly trace is exactly the curve published in
        `adaptive_pr_auc_over_time.png`. Inserting surrogate calls
        inside the inner loop would alter timing (CPU ledger) and
        risk subtle off-by-one bugs. The post-pass design relies on
        `retrain_log` carrying the trigger timestamp + reason, which
        the Phase 7 orchestrator already records.
    """

    def __init__(
        self,
        windows: pd.DataFrame,
        streams_wide: pd.DataFrame,
        samples: pd.DataFrame,
        events: pd.DataFrame,
        base_detector_factory,
        drift_detector_factories: Mapping[str, "object"],
        surrogates: SurrogateBundle,
        cfg: DemoConfig,
    ) -> None:
        surrogates.validate()
        self.windows = windows
        self.streams_wide = streams_wide
        self.samples = samples
        self.events = events
        self.base_detector_factory = base_detector_factory
        self.drift_detector_factories = dict(drift_detector_factories)
        self.surrogates = surrogates
        self.cfg = cfg

    # -- public API ---------------------------------------------------------

    def run(self) -> DemoResult:
        """Run walk-forward replay + post-pass surrogate query."""
        # 1. Phase 7 walk-forward (drift-triggered-filtered)
        adaptive_cfg = OrchestratorConfig(
            feature_cols=list(self.cfg.feature_cols),
            warmup_max_phase_id=self.cfg.warmup_max_phase_id,
            retrain_window_s=self.cfg.retrain_window_s,
            retrain_score_filter_quantile=self.cfg.filter_quantile,
            score_chunk_size=self.cfg.score_chunk_size,
            random_state=self.cfg.random_state,
        )
        adaptive = AdaptiveOrchestrator(
            windows=self.windows,
            streams_wide=self.streams_wide,
            base_detector_factory=self.base_detector_factory,
            drift_detector_factories=self.drift_detector_factories,
            cfg=adaptive_cfg,
        )
        strategy = DriftTriggeredStrategy(
            cooldown_s=self.cfg.drift_cooldown_s,
            watch_streams=list(self.cfg.watch_streams),
        )
        run_result = adaptive.run(strategy)

        # 2. Post-pass: surrogate-driven intervention per drift-triggered retrain
        interventions = self._build_interventions(run_result.retrain_log)

        return DemoResult(
            per_window=run_result.per_window,
            retrain_log=run_result.retrain_log,
            interventions=interventions,
            base_detector_name=run_result.base_detector_name,
        )

    # -- internals ----------------------------------------------------------

    def _build_interventions(self, retrain_log: pd.DataFrame) -> list[Intervention]:
        """One Intervention per drift-triggered retrain row.

        The Phase 7 retrain_log uses ``reason`` strings of the form
        ``"drift:<stream_name>"`` for drift-triggered fits and
        ``"warmup"`` / ``"periodic"`` for the others. We pick the
        former and query the surrogate once per row.
        """
        if retrain_log.empty:
            return []
        drift_rows = retrain_log[
            retrain_log["reason"].fillna("").str.startswith("drift:")
        ].reset_index(drop=True)
        out: list[Intervention] = []
        for _, row in drift_rows.iterrows():
            t_trigger = float(row["t_s"])
            reason = str(row["reason"])
            streams_fired = tuple(
                s.strip() for s in reason.removeprefix("drift:").split(",") if s.strip()
            )
            iv = self._query_surrogate_for_trigger(t_trigger, streams_fired)
            if iv is not None:
                out.append(iv)
        return out

    def _query_surrogate_for_trigger(
        self, t_trigger_s: float, streams_fired: tuple[str, ...],
    ) -> Intervention | None:
        """Build a single Intervention for one drift-trigger timestamp."""
        # 2a. Deployment context
        ctx = build_context(
            self.samples, t_now_s=t_trigger_s,
            context_window_s=self.cfg.context_window_s,
        )
        if not ctx.is_finite():
            # Skip: not enough samples in the context window to query the surrogate.
            return None

        # 2b. Current config
        cur = current_config(self.events, t_trigger_s)
        if cur is None:
            return None

        # 2c. Inverse query
        constraints = [
            Constraint("hosr",           ">=", self.cfg.constraint_hosr),
            Constraint("rlf_rate",       "<=", self.cfg.constraint_rlf),
            Constraint("ping_pong_rate", "<=", self.cfg.constraint_pp),
        ]
        try:
            inv: InverseQueryResult = inverse_query(
                surrogates=self.surrogates.point_models,
                scenario_features=ctx.to_surrogate_dict(),
                constraints=constraints,
                score_target="hosr",
                score_direction="max",
                interval_quantile_surrogates=self.surrogates.interval_models,
            )
        except ValueError:
            return None

        # 2d. Pick top-1 feasible (or fall back to top-1 candidate by HOSR
        #     if no feasible row exists — at least we emit something for
        #     the figure)
        if inv.n_feasible > 0:
            top = inv.top(1).iloc[0].to_dict()
        else:
            # Sort all candidates by HOSR desc, take #1
            cand_sorted = inv.candidates.sort_values("hosr", ascending=False)
            top = cand_sorted.iloc[0].to_dict()

        rec_cfg = {
            "ttt_ms":    float(top["ttt_ms"]),
            "hyst_db":   float(top["hyst_db"]),
            "a3_off_db": float(top["a3_off_db"]),
        }

        # 2e. Counterfactual: predict KPIs for current cfg under SAME context
        x_current = self._build_feature_row(cur, ctx)
        x_recommended = self._build_feature_row(rec_cfg, ctx)

        pred_current = {
            t: float(self.surrogates.point_models[t].predict(x_current)[0])
            for t in TARGET_COLS
        }
        pred_recommended = {
            t: float(top.get(t, float("nan")))
            for t in TARGET_COLS
        }
        # If the inverse_query result didn't carry every target prediction
        # (unusual), fall back to a direct point predict
        for t in TARGET_COLS:
            if not np.isfinite(pred_recommended[t]):
                pred_recommended[t] = float(
                    self.surrogates.point_models[t].predict(x_recommended)[0]
                )

        # Conformal intervals (if present in the inverse_query output)
        pred_lo = {
            t: float(top[f"{t}_lo"]) if f"{t}_lo" in top else float("nan")
            for t in TARGET_COLS
        }
        pred_hi = {
            t: float(top[f"{t}_hi"]) if f"{t}_hi" in top else float("nan")
            for t in TARGET_COLS
        }

        cfg_changed = any(
            abs(rec_cfg[k] - cur[k]) > 1e-9 for k in ("ttt_ms", "hyst_db", "a3_off_db")
        )

        return Intervention(
            t_trigger_s=t_trigger_s,
            drift_streams_fired=streams_fired,
            context=ctx,
            current_cfg=cur,
            recommended_cfg=rec_cfg,
            pred_current=pred_current,
            pred_recommended=pred_recommended,
            pred_recommended_lo=pred_lo,
            pred_recommended_hi=pred_hi,
            n_feasible=int(inv.n_feasible),
            n_candidates=int(len(inv.candidates)),
            cfg_changed=cfg_changed,
        )

    @staticmethod
    def _build_feature_row(
        cfg_knobs: dict[str, float], ctx: DeploymentContext,
    ) -> pd.DataFrame:
        """Assemble a single-row DataFrame matching the surrogate's
        feature schema (3 controlled + 9 scenario)."""
        row = {
            "ttt_ms":    float(cfg_knobs["ttt_ms"]),
            "hyst_db":   float(cfg_knobs["hyst_db"]),
            "a3_off_db": float(cfg_knobs["a3_off_db"]),
        }
        row.update(ctx.to_surrogate_dict())
        # Column order must match FEATURE_COLS (controlled first, then scenario)
        col_order = [
            "ttt_ms", "hyst_db", "a3_off_db",
            *SCENARIO_COLS,
        ]
        return pd.DataFrame([[row[c] for c in col_order]], columns=col_order)

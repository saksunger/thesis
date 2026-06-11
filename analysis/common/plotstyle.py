"""Shared Matplotlib style for thesis figures.

The figures are included in the LaTeX document at roughly ``\\textwidth``
(~6 inches). Because the plotting scripts historically used wide canvases
(13--15 inches) with small fonts (7--10 pt), the text became unreadable
once the figure was downscaled to fit the page. ``apply_thesis_style``
raises the base font sizes and the export DPI so that, after downscaling,
on-page text stays legible. Call it once before building any figure::

    from analysis.common.plotstyle import apply_thesis_style
    apply_thesis_style()

It only touches rcParams that control text size and export quality, so it
is safe to call from every plotting script without altering plot logic.
"""

from __future__ import annotations

import matplotlib as mpl


def apply_thesis_style() -> None:
    """Set legible, print-oriented Matplotlib defaults (idempotent)."""
    mpl.rcParams.update(
        {
            "font.size": 13,
            "axes.titlesize": 14,
            "axes.labelsize": 13,
            "xtick.labelsize": 12,
            "ytick.labelsize": 12,
            "legend.fontsize": 11,
            "figure.titlesize": 15,
            "savefig.dpi": 200,
            "figure.dpi": 200,
            "savefig.bbox": "tight",
        }
    )

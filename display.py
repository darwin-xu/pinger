"""Rich terminal dashboard for the VPS monitor."""
from __future__ import annotations

from datetime import datetime

from rich import box
from rich.table import Table

from formatting import fmt_duration

# Eight-step block characters (▁ = quietest, █ = loudest)
_BLOCKS = "▁▂▃▄▅▆▇█"


# ── Sparkline ─────────────────────────────────────────────────────────────────

def _sparkline(values: list[float], width: int = 10) -> str:
    """Convert a list of floats into a unicode block sparkline."""
    if not values:
        return "─" * width
    recent = values[-width:]
    mn, mx = min(recent), max(recent)
    span = (mx - mn) or 1.0
    chars = [
        _BLOCKS[round((v - mn) / span * (len(_BLOCKS) - 1))]
        for v in recent
    ]
    # Left-pad with dashes so the column width is stable
    return "─" * max(0, width - len(chars)) + "".join(chars)


# ── Color helpers ─────────────────────────────────────────────────────────────

def _color_val(
    val: float | None,
    warn: float,
    crit: float,
    higher_is_worse: bool = True,
    fmt: str = ".2f",
    suffix: str = "",
    use_duration: bool = False,
) -> str:
    """Return a Rich markup string for a numeric value, color-coded by thresholds."""
    if val is None:
        return "[dim]—[/dim]"
    if use_duration:
        s = fmt_duration(val)
    else:
        s = format(val, fmt) + suffix
    if higher_is_worse:
        color = "red" if val >= crit else "yellow" if val >= warn else "green"
    else:
        color = "red" if val <= crit else "yellow" if val <= warn else "green"
    return f"[{color}]{s}[/{color}]"


def _status_icon(ping_d: dict, tcp_d: dict) -> str:
    if not ping_d:
        return "[dim]?[/dim]"
    loss = ping_d.get("loss", 100.0)
    ok   = ping_d.get("success", False)
    if not ok or loss >= 50:
        return "[red]✗[/red]"
    if loss >= 1 or not tcp_d.get("success"):
        return "[yellow]⚠[/yellow]"
    return "[green]✓[/green]"


def _ago(ts: str | None) -> str:
    """Human-readable 'time since ts' string."""
    if not ts:
        return "[dim]—[/dim]"
    try:
        delta = int((datetime.utcnow() - datetime.fromisoformat(ts)).total_seconds())
    except Exception:
        return "[dim]—[/dim]"
    if delta < 5:
        return "[green]now[/green]"
    if delta < 60:
        return f"[dim]{delta}s[/dim]"
    if delta < 3600:
        return f"[dim]{delta // 60}m[/dim]"
    return f"[red]{delta // 3600}h[/red]"


# ── Main table builder ────────────────────────────────────────────────────────

def build_table(
    hosts: list[dict],
    results: dict,
    thresholds: dict,
    history: dict,
) -> Table:
    thr = {
        "pw": thresholds.get("ping_warn_ms",  100),
        "pc": thresholds.get("ping_crit_ms",  300),
        "lw": thresholds.get("loss_warn_pct",   1),
        "lc": thresholds.get("loss_crit_pct",   5),
        "tw": thresholds.get("tcp_warn_ms",   150),
        "tc": thresholds.get("tcp_crit_ms",   500),
        "bw": thresholds.get("bw_warn_mbps",   50),
        "bc": thresholds.get("bw_crit_mbps",   10),
    }

    table = Table(
        title=(
            f"[bold cyan]VPS Network Monitor[/bold cyan]  ·  "
            f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]"
        ),
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=False,
        expand=True,
        padding=(0, 1),
    )

    table.add_column("#",        style="dim",  justify="right", width=3)
    table.add_column("Name",     style="bold", no_wrap=True)
    table.add_column("Host",     style="dim",  no_wrap=True)
    table.add_column("",         justify="center", width=2)   # status icon
    table.add_column("Ping avg", justify="right")
    table.add_column("Jitter",   justify="right")
    table.add_column("Loss",     justify="right")
    table.add_column("TCP RTT",  justify="right")
    table.add_column("↓ Mbps",   justify="right")
    table.add_column("↑ Mbps",   justify="right")
    table.add_column("Trend",    no_wrap=True, width=12)
    table.add_column("Updated",  justify="right")

    for i, h in enumerate(hosts, 1):
        name = h["name"]
        host = h["host"]
        r        = results.get(name, {})
        ping_d   = r.get("ping",   {})
        tcp_d    = r.get("tcp",    {})
        iperf_d  = r.get("iperf3", {})

        avg    = ping_d.get("avg")    if ping_d.get("success") else None
        jitter = ping_d.get("jitter") if ping_d.get("success") else None
        loss   = ping_d.get("loss")   if ping_d                 else None

        tcp_rtt      = tcp_d.get("rtt")          if tcp_d.get("success")   else None
        download_mbps = iperf_d.get("download_mbps") if iperf_d.get("success") else None
        upload_mbps   = iperf_d.get("upload_mbps")   if iperf_d.get("success") else None

        hist  = list(history.get(name, []))
        spark = _sparkline(hist)

        # Jitter thresholds derived from ping thresholds (30 % of ping thresholds)
        jw = thr["pw"] * 0.3
        jc = thr["pc"] * 0.3

        table.add_row(
            str(i),
            name,
            host,
            _status_icon(ping_d, tcp_d),
            _color_val(avg,          thr["pw"], thr["pc"], use_duration=True),
            _color_val(jitter,       jw,        jc,        use_duration=True),
            _color_val(loss,         thr["lw"], thr["lc"], suffix="%", fmt=".2f"),
            _color_val(tcp_rtt,      thr["tw"], thr["tc"], use_duration=True),
            _color_val(download_mbps, thr["bw"], thr["bc"], higher_is_worse=False, fmt=".0f"),
            _color_val(upload_mbps,   thr["bw"], thr["bc"], higher_is_worse=False, fmt=".0f"),
            f"[cyan]{spark}[/cyan]",
            _ago(ping_d.get("ts")),
        )

    return table

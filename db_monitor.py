#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║         MONITOR DE SALUD DE BASE DE DATOS MySQL              ║
║         Versión 1.0 - Python + Rich Dashboard                ║
╚══════════════════════════════════════════════════════════════╝
"""

import configparser
import sys
import time
import signal
from datetime import datetime, timedelta
from pathlib import Path

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except ImportError:
    print("[ERROR] Módulo 'mysql-connector-python' no instalado.")
    print("        Ejecuta: pip install mysql-connector-python rich")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich.columns import Columns
    from rich.align import Align
    from rich import box
    from rich.style import Style
    from rich.progress import BarColumn, Progress, TextColumn
except ImportError:
    print("[ERROR] Módulo 'rich' no instalado.")
    print("        Ejecuta: pip install mysql-connector-python rich")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "config.ini"
console = Console()
_running = True


def signal_handler(sig, frame):
    global _running
    _running = False


signal.signal(signal.SIGINT, signal_handler)


# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not CONFIG_FILE.exists():
        console.print(f"[bold red]✘ No se encontró config.ini en {CONFIG_FILE}[/]")
        sys.exit(1)
    cfg.read(CONFIG_FILE, encoding="utf-8")
    return cfg


# ─────────────────────────────────────────────────────────────
# CONEXIÓN
# ─────────────────────────────────────────────────────────────
def get_connection(cfg: configparser.ConfigParser):
    return mysql.connector.connect(
        host=cfg.get("mysql", "host", fallback="localhost"),
        port=cfg.getint("mysql", "port", fallback=3306),
        user=cfg.get("mysql", "user"),
        password=cfg.get("mysql", "password"),
        database=cfg.get("mysql", "database", fallback="db_health_monitor"),
        connection_timeout=cfg.getint("mysql", "connect_timeout", fallback=10),
        autocommit=True,
    )


# ─────────────────────────────────────────────────────────────
# RECOLECCIÓN DE MÉTRICAS
# ─────────────────────────────────────────────────────────────
def fetch_global_status(cursor) -> dict:
    cursor.execute("SHOW GLOBAL STATUS")
    return {row[0]: row[1] for row in cursor.fetchall()}


def fetch_global_variables(cursor) -> dict:
    cursor.execute("SHOW GLOBAL VARIABLES")
    return {row[0]: row[1] for row in cursor.fetchall()}


def fetch_processlist(cursor) -> list:
    cursor.execute("""
        SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE,
               LEFT(IFNULL(INFO,''), 80) AS INFO
        FROM information_schema.PROCESSLIST
        WHERE COMMAND != 'Sleep'
        ORDER BY TIME DESC
    """)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetch_db_sizes(cursor) -> list:
    cursor.execute("""
        SELECT
            table_schema AS db_name,
            ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS size_mb,
            COUNT(*) AS tables
        FROM information_schema.TABLES
        WHERE table_schema NOT IN
              ('information_schema','performance_schema','mysql','sys')
        GROUP BY table_schema
        ORDER BY size_mb DESC
        LIMIT 10
    """)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetch_replication(cursor) -> dict | None:
    try:
        cursor.execute("SHOW SLAVE STATUS")
        row = cursor.fetchone()
        if row:
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))
    except Exception:
        pass
    return None


def collect_metrics(conn) -> dict:
    cursor = conn.cursor()
    status = fetch_global_status(cursor)
    variables = fetch_global_variables(cursor)
    processes = fetch_processlist(cursor)
    db_sizes = fetch_db_sizes(cursor)
    replication = fetch_replication(cursor)
    cursor.close()

    # ── Conexiones ──────────────────────────────────────────
    max_conn = int(variables.get("max_connections", 1))
    threads_conn = int(status.get("Threads_connected", 0))
    conn_pct = round((threads_conn / max_conn) * 100, 2) if max_conn else 0

    # ── Buffer Pool ─────────────────────────────────────────
    bp_reads = int(status.get("Innodb_buffer_pool_reads", 0))
    bp_read_reqs = int(status.get("Innodb_buffer_pool_read_requests", 1))
    hit_ratio = round((1 - bp_reads / bp_read_reqs) * 100, 2) if bp_read_reqs else 100.0

    bp_total = int(status.get("Innodb_buffer_pool_pages_total", 0))
    bp_free = int(status.get("Innodb_buffer_pool_pages_free", 0))
    bp_dirty = int(status.get("Innodb_buffer_pool_pages_dirty", 0))
    bp_used_pct = round(((bp_total - bp_free) / bp_total) * 100, 2) if bp_total else 0

    bp_size_mb = round(int(variables.get("innodb_buffer_pool_size", 0)) / 1024 / 1024, 0)

    # ── QPS ─────────────────────────────────────────────────
    uptime = int(status.get("Uptime", 1))
    questions = int(status.get("Questions", 0))
    qps = round(questions / uptime, 2) if uptime else 0

    # ── Uptime formateado ───────────────────────────────────
    td = timedelta(seconds=uptime)
    days = td.days
    hours, rem = divmod(td.seconds, 3600)
    mins, secs = divmod(rem, 60)
    uptime_str = f"{days}d {hours:02}h {mins:02}m {secs:02}s"

    return {
        "timestamp": datetime.now(),
        # Conexiones
        "max_connections": max_conn,
        "threads_connected": threads_conn,
        "threads_running": int(status.get("Threads_running", 0)),
        "threads_cached": int(status.get("Threads_cached", 0)),
        "threads_created": int(status.get("Threads_created", 0)),
        "connection_pct": conn_pct,
        # Rendimiento
        "questions": questions,
        "qps": qps,
        "slow_queries": int(status.get("Slow_queries", 0)),
        "com_select": int(status.get("Com_select", 0)),
        "com_insert": int(status.get("Com_insert", 0)),
        "com_update": int(status.get("Com_update", 0)),
        "com_delete": int(status.get("Com_delete", 0)),
        # InnoDB
        "innodb_hit_ratio": hit_ratio,
        "bp_size_mb": bp_size_mb,
        "bp_used_pct": bp_used_pct,
        "bp_pages_total": bp_total,
        "bp_pages_free": bp_free,
        "bp_pages_dirty": bp_dirty,
        # Uptime
        "uptime_seconds": uptime,
        "uptime_str": uptime_str,
        # Version
        "version": variables.get("version", "?"),
        # Listas
        "processes": processes,
        "db_sizes": db_sizes,
        "replication": replication,
    }


# ─────────────────────────────────────────────────────────────
# EVALUACIÓN DE ALERTAS
# ─────────────────────────────────────────────────────────────
def evaluate_alerts(metrics: dict, cfg: configparser.ConfigParser) -> list[dict]:
    alerts = []

    def thr(section, key, default):
        return cfg.getfloat(section, key, fallback=default)

    checks = [
        (
            "connection_pct",
            metrics["connection_pct"],
            thr("thresholds", "connections_warning", 70),
            thr("thresholds", "connections_critical", 90),
            "Uso de Conexiones",
            "%",
        ),
        (
            "innodb_hit_ratio",
            metrics["innodb_hit_ratio"],
            thr("thresholds", "cache_hit_warning", 95),
            thr("thresholds", "cache_hit_critical", 85),
            "InnoDB Hit Ratio",
            "%",
        ),
        (
            "threads_running",
            metrics["threads_running"],
            thr("thresholds", "threads_running_warning", 20),
            thr("thresholds", "threads_running_critical", 50),
            "Threads Corriendo",
            "",
        ),
        (
            "slow_queries",
            metrics["slow_queries"],
            thr("thresholds", "slow_queries_warning", 100),
            thr("thresholds", "slow_queries_critical", 500),
            "Consultas Lentas",
            "",
        ),
    ]

    for key, value, warn, crit, label, unit in checks:
        # Para hit_ratio: umbrales invertidos (menor = peor)
        if key == "innodb_hit_ratio":
            if value < crit:
                sev = "CRITICAL"
            elif value < warn:
                sev = "WARNING"
            else:
                continue
        else:
            if value >= crit:
                sev = "CRITICAL"
            elif value >= warn:
                sev = "WARNING"
            else:
                continue

        alerts.append(
            {
                "severity": sev,
                "metric": label,
                "value": f"{value}{unit}",
                "threshold": f"WARN={warn}{unit} CRIT={crit}{unit}",
                "message": f"{label} en {value}{unit} supera umbral {sev}",
            }
        )

    return alerts


def save_snapshot(conn, metrics: dict, alerts: list):
    status_val = "OK"
    if any(a["severity"] == "CRITICAL" for a in alerts):
        status_val = "CRITICAL"
    elif any(a["severity"] == "WARNING" for a in alerts):
        status_val = "WARNING"

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO health_snapshots (
            max_connections, threads_connected, threads_running, threads_cached,
            threads_created, connection_pct, questions, qps, slow_queries,
            innodb_buffer_pool_size, innodb_buffer_pool_reads,
            innodb_buffer_pool_read_reqs, innodb_hit_ratio,
            innodb_buffer_pool_pages_total, innodb_buffer_pool_pages_free,
            innodb_buffer_pool_pages_dirty, uptime_seconds, status
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        metrics["max_connections"], metrics["threads_connected"],
        metrics["threads_running"], metrics["threads_cached"],
        metrics["threads_created"], metrics["connection_pct"],
        metrics["questions"], metrics["qps"], metrics["slow_queries"],
        int(metrics["bp_size_mb"] * 1024 * 1024),
        metrics.get("bp_pages_total", 0),
        metrics.get("bp_pages_total", 0),
        metrics["innodb_hit_ratio"],
        metrics["bp_pages_total"], metrics["bp_pages_free"],
        metrics["bp_pages_dirty"], metrics["uptime_seconds"],
        status_val,
    ))

    for alert in alerts:
        cur.execute("""
            INSERT INTO alert_log (severity, metric_name, metric_value, threshold, message)
            VALUES (%s,%s,%s,%s,%s)
        """, (
            alert["severity"], alert["metric"],
            alert["value"], alert["threshold"], alert["message"],
        ))

    cur.close()


# ─────────────────────────────────────────────────────────────
# CONSTRUCCIÓN DEL DASHBOARD
# ─────────────────────────────────────────────────────────────
STATUS_COLOR = {"OK": "bold green", "WARNING": "bold yellow", "CRITICAL": "bold red"}
SEV_COLOR = {"INFO": "cyan", "WARNING": "yellow", "CRITICAL": "red"}


def status_badge(value, warn, crit, invert=False) -> Text:
    if invert:
        sev = "CRITICAL" if value < crit else "WARNING" if value < warn else "OK"
    else:
        sev = "CRITICAL" if value >= crit else "WARNING" if value >= warn else "OK"
    icons = {"OK": "✔", "WARNING": "⚠", "CRITICAL": "✘"}
    return Text(f"{icons[sev]} {value}", style=STATUS_COLOR[sev])


def mini_bar(pct: float, width: int = 20) -> str:
    filled = int(min(pct, 100) / 100 * width)
    empty = width - filled
    return "█" * filled + "░" * empty


def build_header(metrics: dict) -> Panel:
    ts = metrics["timestamp"].strftime("%Y-%m-%d  %H:%M:%S")
    host_info = f"[bold cyan]MySQL {metrics['version']}[/]  |  ⏱ Uptime: [white]{metrics['uptime_str']}[/]"
    title = Text.assemble(
        ("  ██ MONITOR DE SALUD MYSQL ██  ", "bold white on #1a1a2e"),
    )
    return Panel(
        Align.center(
            Text.assemble(
                host_info, "   │   📅 ", (ts, "bold white")
            )
        ),
        title=title,
        border_style="#4a9eff",
        padding=(0, 2),
    )


def build_connections_panel(metrics: dict, cfg) -> Panel:
    conn_pct = metrics["connection_pct"]
    w = cfg.getfloat("thresholds", "connections_warning", fallback=70)
    c = cfg.getfloat("thresholds", "connections_critical", fallback=90)

    bar_color = "red" if conn_pct >= c else "yellow" if conn_pct >= w else "green"
    bar = f"[{bar_color}]{mini_bar(conn_pct)}[/] {conn_pct:.1f}%"

    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column("Métrica", style="dim", width=22)
    table.add_column("Valor", justify="right")

    table.add_row("Conectados / Máx",
                  f"[bold]{metrics['threads_connected']}[/] / {metrics['max_connections']}")
    table.add_row("Uso de conexiones", bar)
    table.add_row("Threads ejecutando", status_badge(
        metrics["threads_running"],
        cfg.getfloat("thresholds", "threads_running_warning", fallback=20),
        cfg.getfloat("thresholds", "threads_running_critical", fallback=50),
    ))
    table.add_row("Threads en caché", f"[cyan]{metrics['threads_cached']}[/]")
    table.add_row("Threads creados", f"{metrics['threads_created']:,}")

    return Panel(table, title="[bold blue]🔌 Conexiones[/]",
                 border_style="#4a9eff", padding=(1, 2))


def build_performance_panel(metrics: dict) -> Panel:
    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column("Métrica", style="dim", width=22)
    table.add_column("Valor", justify="right")

    table.add_row("Queries por segundo (QPS)", f"[bold green]{metrics['qps']:.2f}[/]")
    table.add_row("Total queries",    f"{metrics['questions']:,}")
    table.add_row("Consultas lentas", f"[yellow]{metrics['slow_queries']:,}[/]")
    table.add_row("SELECTs",          f"{metrics['com_select']:,}")
    table.add_row("INSERTs",          f"{metrics['com_insert']:,}")
    table.add_row("UPDATEs",          f"{metrics['com_update']:,}")
    table.add_row("DELETEs",          f"{metrics['com_delete']:,}")

    return Panel(table, title="[bold green]⚡ Rendimiento[/]",
                 border_style="#00c896", padding=(1, 2))


def build_innodb_panel(metrics: dict, cfg) -> Panel:
    hit = metrics["innodb_hit_ratio"]
    hw = cfg.getfloat("thresholds", "cache_hit_warning", fallback=95)
    hc = cfg.getfloat("thresholds", "cache_hit_critical", fallback=85)

    hit_color = "red" if hit < hc else "yellow" if hit < hw else "green"
    hit_bar = f"[{hit_color}]{mini_bar(hit)}[/] {hit:.2f}%"

    bp_used = metrics["bp_used_pct"]
    bp_bar = f"[cyan]{mini_bar(bp_used)}[/] {bp_used:.1f}%"

    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column("Métrica", style="dim", width=26)
    table.add_column("Valor", justify="right")

    table.add_row("Buffer Pool", f"[bold]{metrics['bp_size_mb']:.0f} MB[/]")
    table.add_row("Uso del Buffer Pool", bp_bar)
    table.add_row("Cache Hit Ratio", hit_bar)
    table.add_row("Páginas totales", f"{metrics['bp_pages_total']:,}")
    table.add_row("Páginas libres", f"[green]{metrics['bp_pages_free']:,}[/]")
    table.add_row("Páginas sucias", f"[yellow]{metrics['bp_pages_dirty']:,}[/]")

    return Panel(table, title="[bold magenta]🗄️  InnoDB Buffer Pool[/]",
                 border_style="#c896ff", padding=(1, 2))


def build_processes_panel(metrics: dict, max_rows: int = 10) -> Panel:
    procs = metrics["processes"][:max_rows]

    if not procs:
        return Panel(
            Align.center(Text("No hay procesos activos", style="dim italic")),
            title="[bold yellow]🔄 Procesos Activos[/]",
            border_style="#ffaa00",
            padding=(1, 2),
        )

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold white on #2d2d44",
        padding=(0, 1),
    )
    table.add_column("ID", style="dim", width=7)
    table.add_column("Usuario", width=14)
    table.add_column("BD", width=14)
    table.add_column("Cmd", width=10)
    table.add_column("Seg", justify="right", width=6)
    table.add_column("Estado", width=18)
    table.add_column("Query", width=40)

    for p in procs:
        t = int(p.get("TIME") or 0)
        time_style = "red bold" if t > 30 else "yellow" if t > 10 else "white"
        table.add_row(
            str(p.get("ID", "")),
            str(p.get("USER", "")),
            str(p.get("DB") or "-"),
            str(p.get("COMMAND", "")),
            Text(str(t), style=time_style),
            str(p.get("STATE") or ""),
            str(p.get("INFO") or ""),
        )

    return Panel(table, title="[bold yellow]🔄 Procesos Activos[/]",
                 border_style="#ffaa00", padding=(0, 1))


def build_db_sizes_panel(metrics: dict) -> Panel:
    sizes = metrics["db_sizes"]

    if not sizes:
        return Panel(
            Align.center(Text("Sin bases de datos de usuario", style="dim italic")),
            title="[bold cyan]💾 Tamaño por BD[/]",
            border_style="#00c8ff",
        )

    table = Table(box=box.SIMPLE_HEAD, show_header=True,
                  header_style="bold white on #2d2d44", padding=(0, 1))
    table.add_column("Base de Datos", width=22)
    table.add_column("Tamaño", justify="right", width=12)
    table.add_column("Tablas", justify="right", width=8)

    for db in sizes:
        size = float(db.get("size_mb") or 0)
        size_str = f"{size:.2f} MB" if size < 1024 else f"{size/1024:.2f} GB"
        table.add_row(
            str(db.get("db_name", "")),
            f"[bold]{size_str}[/]",
            str(db.get("tables", 0)),
        )

    return Panel(table, title="[bold cyan]💾 Tamaño por BD[/]",
                 border_style="#00c8ff", padding=(0, 1))


def build_alerts_panel(alerts: list) -> Panel:
    if not alerts:
        return Panel(
            Align.center(Text("✔  Todo en orden – sin alertas activas",
                              style="bold green")),
            title="[bold green]🔔 Alertas[/]",
            border_style="green",
            padding=(1, 2),
        )

    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column("Sev", width=10)
    table.add_column("Métrica", width=20)
    table.add_column("Valor", width=12)
    table.add_column("Mensaje")

    for a in alerts:
        sev_col = SEV_COLOR.get(a["severity"], "white")
        table.add_row(
            Text(a["severity"], style=f"bold {sev_col}"),
            a["metric"],
            Text(a["value"], style="bold"),
            a["message"],
        )

    border = "red" if any(a["severity"] == "CRITICAL" for a in alerts) else "yellow"
    return Panel(table, title="[bold red]🔔 Alertas Activas[/]",
                 border_style=border, padding=(1, 2))


def build_replication_panel(metrics: dict, cfg) -> Panel | None:
    rep = metrics.get("replication")
    if not rep:
        return None

    lag = rep.get("Seconds_Behind_Master")
    lag_str = str(lag) if lag is not None else "N/A"
    lw = cfg.getfloat("thresholds", "replication_lag_warning", fallback=10)
    lc = cfg.getfloat("thresholds", "replication_lag_critical", fallback=30)

    try:
        lag_num = float(lag or 0)
        lag_color = "red" if lag_num >= lc else "yellow" if lag_num >= lw else "green"
    except (TypeError, ValueError):
        lag_color = "white"

    io_ok = rep.get("Slave_IO_Running", "No") == "Yes"
    sql_ok = rep.get("Slave_SQL_Running", "No") == "Yes"

    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column("", style="dim", width=22)
    table.add_column("", justify="right")
    table.add_row("IO Thread",
                  Text("✔ Running" if io_ok else "✘ Stopped",
                       style="green" if io_ok else "red"))
    table.add_row("SQL Thread",
                  Text("✔ Running" if sql_ok else "✘ Stopped",
                       style="green" if sql_ok else "red"))
    table.add_row("Lag (segundos)",
                  Text(lag_str, style=f"bold {lag_color}"))
    table.add_row("Fuente", str(rep.get("Master_Host", "?")))

    return Panel(table, title="[bold yellow]🔁 Replicación[/]",
                 border_style="yellow", padding=(1, 2))


def build_dashboard(metrics: dict, alerts: list, cfg) -> str:
    """Renderiza el dashboard completo como texto Rich."""
    parts = []
    parts.append(build_header(metrics))
    parts.append(Columns([
        build_connections_panel(metrics, cfg),
        build_performance_panel(metrics),
        build_innodb_panel(metrics, cfg),
    ], equal=True, expand=True))
    parts.append(build_alerts_panel(alerts))

    rep_panel = build_replication_panel(metrics, cfg)
    if rep_panel:
        parts.append(rep_panel)

    parts.append(Columns([
        build_processes_panel(metrics, cfg.getint("monitor", "max_processes_shown", fallback=10)),
        build_db_sizes_panel(metrics),
    ], equal=True, expand=True))

    footer = Text.assemble(
        ("  Refrescando cada ", "dim"),
        (f"{cfg.getint('monitor','refresh_interval', fallback=5)}s", "bold cyan"),
        ("  │  ", "dim"),
        ("Ctrl+C para salir", "dim"),
        ("  ", ""),
    )
    parts.append(Align.center(footer))

    return parts


# ─────────────────────────────────────────────────────────────
# BUCLE PRINCIPAL
# ─────────────────────────────────────────────────────────────
def main():
    console.print(Panel.fit(
        "[bold white]Iniciando Monitor de Salud MySQL...[/]",
        border_style="#4a9eff",
    ))

    cfg = load_config()
    refresh = cfg.getint("monitor", "refresh_interval", fallback=5)
    save_hist = cfg.getboolean("monitor", "save_history", fallback=True)

    # Intentar conectar
    conn = None
    try:
        conn = get_connection(cfg)
        console.print("[bold green]✔ Conexión a MySQL establecida.[/]")
        time.sleep(0.8)
    except MySQLError as e:
        console.print(f"[bold red]✘ Error de conexión: {e}[/]")
        console.print("\n[yellow]Verifica los datos en config.ini[/]")
        sys.exit(1)

    with Live(console=console, screen=True, refresh_per_second=1) as live:
        while _running:
            try:
                if not conn.is_connected():
                    conn.reconnect(attempts=3, delay=2)

                metrics = collect_metrics(conn)
                alerts = evaluate_alerts(metrics, cfg)

                if save_hist:
                    try:
                        save_snapshot(conn, metrics, alerts)
                    except Exception:
                        pass  # No detener el monitor si falla el guardado

                # Construir layout
                from rich.console import Group
                parts = build_dashboard(metrics, alerts, cfg)
                live.update(Group(*parts))

            except MySQLError as e:
                live.update(Panel(
                    f"[bold red]Error de MySQL: {e}\n\nReconectando...[/]",
                    border_style="red",
                ))
                time.sleep(5)
                try:
                    conn.reconnect(attempts=3, delay=2)
                except Exception:
                    pass

            except Exception as e:
                live.update(Panel(
                    f"[bold red]Error inesperado: {e}[/]",
                    border_style="red",
                ))

            # Esperar intervalo con soporte de SIGINT
            for _ in range(refresh * 10):
                if not _running:
                    break
                time.sleep(0.1)

    if conn and conn.is_connected():
        conn.close()

    console.print("\n[bold cyan]Monitor detenido. ¡Hasta luego! 👋[/]\n")


if __name__ == "__main__":
    main()

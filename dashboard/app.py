"""
MSBDD - Dashboard Visual (RF02)
Dashboard interactivo con Dash + Plotly para visualización de métricas en tiempo real.
Implementa semáforos de riesgo, gráficas de tendencia y panel de diagnóstico.
"""
import os
import requests
import dash
from dash import dcc, html, dash_table
from dash.dependencies import Input, Output, State
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from datetime import datetime

# ─── Configuración ───────────────────────────────────────────────────────────

API_URL = os.getenv("API_URL", "http://backend:8000")
INTERVALO_ACTUALIZACION_MS = int(os.getenv("INTERVALO_DASHBOARD_MS", "15000"))  # 15 segundos

COLORES = {
    "verde": "#28a745",
    "amarillo": "#ffc107",
    "rojo": "#dc3545",
    "fondo": "#0d1117",
    "panel": "#161b22",
    "borde": "#30363d",
    "texto": "#c9d1d9",
    "texto_secundario": "#8b949e",
    "acento": "#58a6ff",
}

NIVEL_COLOR = {
    "verde": COLORES["verde"],
    "amarillo": COLORES["amarillo"],
    "rojo": COLORES["rojo"],
}

# ─── Utilidades de API ───────────────────────────────────────────────────────

def obtener_de_api(ruta: str, timeout: int = 5):
    try:
        resp = requests.get(f"{API_URL}{ruta}", timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


# ─── Componentes de UI ───────────────────────────────────────────────────────

def crear_tarjeta_semaforo(titulo, valor, unidad, nivel, subtitulo=""):
    color = NIVEL_COLOR.get(nivel, COLORES["texto_secundario"])
    emoji = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}.get(nivel, "⚪")
    return dbc.Card([
        dbc.CardBody([
            html.P(titulo, className="text-muted small mb-1", style={"color": COLORES["texto_secundario"]}),
            html.H2(
                f"{valor}{unidad}",
                style={"color": color, "fontWeight": "bold", "fontSize": "2.2rem", "margin": "0"}
            ),
            html.Div([
                html.Span(emoji, style={"fontSize": "1rem"}),
                html.Span(f" {nivel.upper()}", style={"color": color, "fontSize": "0.85rem", "fontWeight": "bold"}),
            ], style={"marginTop": "4px"}),
            html.P(subtitulo, className="text-muted", style={"fontSize": "0.75rem", "marginTop": "4px", "color": COLORES["texto_secundario"]}) if subtitulo else None,
        ])
    ], style={
        "backgroundColor": COLORES["panel"],
        "border": f"1px solid {color}44",
        "borderRadius": "12px",
        "boxShadow": f"0 0 15px {color}22",
    })


def crear_grafica_vacia(titulo):
    fig = go.Figure()
    fig.update_layout(
        title=titulo,
        plot_bgcolor=COLORES["panel"],
        paper_bgcolor=COLORES["panel"],
        font_color=COLORES["texto"],
        xaxis=dict(gridcolor=COLORES["borde"], color=COLORES["texto_secundario"]),
        yaxis=dict(gridcolor=COLORES["borde"], color=COLORES["texto_secundario"]),
        margin=dict(l=40, r=20, t=40, b=30),
    )
    return fig


# ─── Layout ──────────────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    title="MSBDD - Monitor de Salud BD",
    update_title=None,
)

app.layout = dbc.Container([

    # Header
    dbc.Row([
        dbc.Col([
            html.Div([
                html.H1("🗄️ Monitor de Salud de Base de Datos", style={"color": COLORES["acento"], "marginBottom": "4px"}),
                html.P("Universidad Privada de Tacna · EPIS · Sistema Proactivo de Observabilidad PostgreSQL",
                       style={"color": COLORES["texto_secundario"], "fontSize": "0.9rem"}),
            ], style={"padding": "20px 0 10px 0"})
        ])
    ]),

    # Barra de estado
    dbc.Row([
        dbc.Col([
            dbc.Alert(id="alerta-conexion", color="success", className="py-2 px-3", style={"fontSize": "0.85rem"}),
        ])
    ], className="mb-2"),

    # ── Semáforos KPI (RF01) ──
    dbc.Row([
        dbc.Col(html.Div(id="semaforo-cpu"), width=3),
        dbc.Col(html.Div(id="semaforo-memoria"), width=3),
        dbc.Col(html.Div(id="semaforo-conexiones"), width=3),
        dbc.Col(html.Div(id="semaforo-cache"), width=3),
    ], className="mb-3 g-2"),

    # ── Gráficas de tendencia ──
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    dcc.Graph(id="grafica-cpu", figure=crear_grafica_vacia("CPU %"), style={"height": "250px"}),
                ])
            ], style={"backgroundColor": COLORES["panel"], "border": f"1px solid {COLORES['borde']}", "borderRadius": "12px"})
        ], width=6),
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    dcc.Graph(id="grafica-memoria", figure=crear_grafica_vacia("Memoria %"), style={"height": "250px"}),
                ])
            ], style={"backgroundColor": COLORES["panel"], "border": f"1px solid {COLORES['borde']}", "borderRadius": "12px"})
        ], width=6),
    ], className="mb-3 g-2"),

    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    dcc.Graph(id="grafica-conexiones", figure=crear_grafica_vacia("Conexiones Activas"), style={"height": "220px"}),
                ])
            ], style={"backgroundColor": COLORES["panel"], "border": f"1px solid {COLORES['borde']}", "borderRadius": "12px"})
        ], width=8),
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("📊 Estadísticas", style={"backgroundColor": COLORES["panel"], "color": COLORES["texto"], "border": "none"}),
                dbc.CardBody(html.Div(id="panel-resumen"), style={"fontSize": "0.82rem"}),
            ], style={"backgroundColor": COLORES["panel"], "border": f"1px solid {COLORES['borde']}", "borderRadius": "12px", "height": "100%"})
        ], width=4),
    ], className="mb-3 g-2"),

    # ── Panel de alertas (RF03) ──
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("🔔 Alertas Activas", style={"backgroundColor": COLORES["panel"], "color": COLORES["texto"], "border": "none"}),
                dbc.CardBody(html.Div(id="panel-alertas")),
            ], style={"backgroundColor": COLORES["panel"], "border": f"1px solid {COLORES['borde']}", "borderRadius": "12px"})
        ])
    ], className="mb-3"),

    # ── Diagnóstico avanzado (RF04) ──
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader([
                    "🔍 Diagnóstico Avanzado ",
                    dbc.Badge("RF04", color="primary", className="ms-2"),
                ], style={"backgroundColor": COLORES["panel"], "color": COLORES["texto"], "border": "none"}),
                dbc.CardBody([
                    dbc.Tabs([
                        dbc.Tab(html.Div(id="tabla-consultas-lentas"), label="⏱ Consultas Lentas"),
                        dbc.Tab(html.Div(id="tabla-bloqueos"), label="🔒 Bloqueos"),
                        dbc.Tab(html.Div(id="tabla-indices"), label="📋 Índices No Usados"),
                    ]),
                ]),
            ], style={"backgroundColor": COLORES["panel"], "border": f"1px solid {COLORES['borde']}", "borderRadius": "12px"})
        ])
    ], className="mb-3"),

    # Footer
    dbc.Row([
        dbc.Col(html.P(
            f"MSBDD v1.0 · Actualización cada {INTERVALO_ACTUALIZACION_MS // 1000}s · Solo lectura de pg_catalog",
            style={"color": COLORES["texto_secundario"], "fontSize": "0.78rem", "textAlign": "center", "padding": "10px 0"}
        ))
    ]),

    # Intervalo de actualización
    dcc.Interval(id="intervalo-principal", interval=INTERVALO_ACTUALIZACION_MS, n_intervals=0),
    dcc.Interval(id="intervalo-diagnostico", interval=60000, n_intervals=0),  # Diagnóstico cada 60s
    dcc.Store(id="datos-metricas"),
    dcc.Store(id="datos-historial"),

], fluid=True, style={"backgroundColor": COLORES["fondo"], "minHeight": "100vh", "padding": "0 20px"})


# ─── Callbacks ───────────────────────────────────────────────────────────────

@app.callback(
    Output("datos-metricas", "data"),
    Output("datos-historial", "data"),
    Output("alerta-conexion", "children"),
    Output("alerta-conexion", "color"),
    Input("intervalo-principal", "n_intervals"),
)
def actualizar_datos(n):
    metricas = obtener_de_api("/metricas")
    historial = obtener_de_api("/metricas/historial?ultimos_n=80")
    estado = obtener_de_api("/")

    if metricas is None:
        msg = "⚠️ Sin conexión con el backend MSBDD. Reintentando..."
        return None, None, msg, "danger"

    ts = datetime.fromisoformat(metricas["timestamp"]).strftime("%H:%M:%S")
    alertas_n = estado.get("alertas_activas", 0) if estado else 0
    nivel = estado.get("nivel_riesgo_global", "verde") if estado else "verde"
    emoji_nivel = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}.get(nivel, "⚪")

    msg = f"{emoji_nivel} Sistema conectado · Última actualización: {ts} · Alertas activas: {alertas_n}"
    color = {"verde": "success", "amarillo": "warning", "rojo": "danger"}.get(nivel, "success")
    return metricas, historial, msg, color


@app.callback(
    Output("semaforo-cpu", "children"),
    Output("semaforo-memoria", "children"),
    Output("semaforo-conexiones", "children"),
    Output("semaforo-cache", "children"),
    Input("datos-metricas", "data"),
)
def actualizar_semaforos(metricas):
    if not metricas:
        vacio = crear_tarjeta_semaforo("--", "--", "", "verde")
        return vacio, vacio, vacio, vacio

    return (
        crear_tarjeta_semaforo("CPU", metricas["uso_cpu_porcentaje"], "%",
                               metricas["nivel_riesgo_cpu"], f"Umbral: {obtener_configuracion_local('UMBRAL_CPU', 85)}%"),
        crear_tarjeta_semaforo("Memoria", metricas["uso_memoria_porcentaje"], "%",
                               metricas["nivel_riesgo_memoria"], f"BD: {metricas['tamanio_bd_mb']} MB"),
        crear_tarjeta_semaforo("Conexiones Activas", metricas["conexiones_activas"], "",
                               metricas["nivel_riesgo_conexiones"], f"Total: {metricas['conexiones_totales']}"),
        crear_tarjeta_semaforo("Caché Hit", metricas["tasa_cache_hit"], "%",
                               "verde" if metricas["tasa_cache_hit"] >= 90 else "amarillo",
                               f"TPS est.: {metricas['transacciones_por_segundo']}"),
    )


def obtener_configuracion_local(key, default):
    return os.getenv(key, default)


def _estilo_linea(color):
    return dict(color=color, width=2)


def _crear_figura_serie(historial, campo_y, titulo, color, umbral=None, yrange=None):
    if not historial:
        return crear_grafica_vacia(titulo)

    xs = [h["timestamp"] for h in historial]
    ys = [h[campo_y] for h in historial]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=ys,
        mode="lines",
        line=_estilo_linea(color),
        fill="tozeroy",
        fillcolor=f"{color}22",
        name=titulo,
    ))
    if umbral is not None:
        fig.add_hline(y=umbral, line_dash="dot", line_color=COLORES["rojo"],
                      annotation_text=f"Umbral {umbral}", annotation_font_color=COLORES["rojo"])

    fig.update_layout(
        title=dict(text=titulo, font=dict(size=13, color=COLORES["texto"])),
        plot_bgcolor=COLORES["panel"],
        paper_bgcolor=COLORES["panel"],
        font_color=COLORES["texto"],
        xaxis=dict(gridcolor=COLORES["borde"], color=COLORES["texto_secundario"], showticklabels=False),
        yaxis=dict(gridcolor=COLORES["borde"], color=COLORES["texto_secundario"], range=yrange),
        margin=dict(l=40, r=10, t=35, b=10),
        showlegend=False,
    )
    return fig


@app.callback(
    Output("grafica-cpu", "figure"),
    Output("grafica-memoria", "figure"),
    Output("grafica-conexiones", "figure"),
    Input("datos-historial", "data"),
)
def actualizar_graficas(historial):
    umbral_cpu = float(os.getenv("UMBRAL_CPU", "85"))
    umbral_mem = float(os.getenv("UMBRAL_MEMORIA", "80"))
    umbral_conn = int(os.getenv("UMBRAL_CONEXIONES", "80"))
    return (
        _crear_figura_serie(historial, "uso_cpu_porcentaje", "CPU %", COLORES["acento"], umbral_cpu, [0, 100]),
        _crear_figura_serie(historial, "uso_memoria_porcentaje", "Memoria %", "#f0883e", umbral_mem, [0, 100]),
        _crear_figura_serie(historial, "conexiones_activas", "Conexiones Activas", COLORES["verde"], umbral_conn),
    )


@app.callback(
    Output("panel-resumen", "children"),
    Input("intervalo-principal", "n_intervals"),
)
def actualizar_resumen(n):
    resumen = obtener_de_api("/metricas/resumen")
    if not resumen:
        return html.P("Sin datos estadísticos aún.", style={"color": COLORES["texto_secundario"]})

    def fila(label, valor):
        return html.Div([
            html.Span(label, style={"color": COLORES["texto_secundario"]}),
            html.Span(valor, style={"color": COLORES["texto"], "fontWeight": "bold", "float": "right"}),
        ], style={"marginBottom": "6px", "borderBottom": f"1px solid {COLORES['borde']}", "paddingBottom": "4px"})

    cpu = resumen.get("cpu", {})
    mem = resumen.get("memoria", {})
    conn = resumen.get("conexiones", {})
    return [
        html.P(f"📈 {resumen.get('total_puntos', 0)} puntos registrados", style={"color": COLORES["acento"], "marginBottom": "10px", "fontSize": "0.8rem"}),
        fila("CPU Prom.", f"{cpu.get('promedio', '?')}%"),
        fila("CPU Máx.", f"{cpu.get('maximo', '?')}%"),
        fila("Mem Prom.", f"{mem.get('promedio', '?')}%"),
        fila("Mem Máx.", f"{mem.get('maximo', '?')}%"),
        fila("Conn Prom.", f"{conn.get('promedio', '?')}"),
        fila("Conn Máx.", str(conn.get('maximo', '?'))),
    ]


@app.callback(
    Output("panel-alertas", "children"),
    Input("intervalo-principal", "n_intervals"),
)
def actualizar_alertas(n):
    alertas = obtener_de_api("/alertas")
    if alertas is None:
        return html.P("Sin conexión con el backend.", style={"color": COLORES["texto_secundario"]})
    if not alertas:
        return dbc.Alert("✅ Sin alertas activas. El sistema opera dentro de los umbrales configurados.",
                         color="success", className="py-2")

    items = []
    for a in alertas:
        color = {"rojo": "danger", "amarillo": "warning", "verde": "success"}.get(a["nivel"], "secondary")
        ts = datetime.fromisoformat(a["timestamp"]).strftime("%H:%M:%S")
        items.append(
            dbc.Alert([
                html.Strong(f"[{a['tipo'].upper()}] "),
                html.Span(a["mensaje"]),
                html.Span(f" · {ts}", style={"float": "right", "fontSize": "0.8rem"}),
            ], color=color, className="py-2 mb-1", style={"fontSize": "0.85rem"})
        )
    return items


@app.callback(
    Output("tabla-consultas-lentas", "children"),
    Output("tabla-bloqueos", "children"),
    Output("tabla-indices", "children"),
    Input("intervalo-diagnostico", "n_intervals"),
    Input("intervalo-principal", "n_intervals"),
)
def actualizar_diagnostico(n_diag, n_princ):
    diagnostico = obtener_de_api("/diagnostico", timeout=8)

    estilo_tabla = {
        "style_table": {"overflowX": "auto"},
        "style_header": {"backgroundColor": COLORES["fondo"], "color": COLORES["acento"], "fontWeight": "bold", "border": "none"},
        "style_cell": {"backgroundColor": COLORES["panel"], "color": COLORES["texto"], "border": f"1px solid {COLORES['borde']}", "fontSize": "0.8rem", "padding": "6px"},
        "style_data_conditional": [{"if": {"row_index": "odd"}, "backgroundColor": COLORES["fondo"]}],
        "page_size": 8,
    }

    if not diagnostico:
        msg = html.P("Sin datos de diagnóstico.", style={"color": COLORES["texto_secundario"], "padding": "10px"})
        return msg, msg, msg

    # Consultas lentas
    consultas = diagnostico.get("consultas_lentas", [])
    if consultas:
        tabla_consultas = dash_table.DataTable(
            data=[{"PID": c["pid"], "Usuario": c["usuario"], "Duración (s)": c["duracion_segundos"],
                   "Estado": c["estado"], "Consulta": c["texto_consulta"][:80] + "..."} for c in consultas],
            columns=[{"name": col, "id": col} for col in ["PID", "Usuario", "Duración (s)", "Estado", "Consulta"]],
            **estilo_tabla
        )
    else:
        tabla_consultas = dbc.Alert("✅ Sin consultas lentas detectadas.", color="success", className="py-2 mt-2")

    # Bloqueos
    bloqueos = diagnostico.get("bloqueos_activos", [])
    if bloqueos:
        tabla_bloqueos = dash_table.DataTable(
            data=[{"PID Bloqueado": b["pid_bloqueado"], "PID Bloqueante": b["pid_bloqueante"],
                   "Relación": b["relacion_bloqueada"], "Tipo": b["tipo_bloqueo"],
                   "Espera (s)": b["duracion_espera_segundos"]} for b in bloqueos],
            columns=[{"name": col, "id": col} for col in ["PID Bloqueado", "PID Bloqueante", "Relación", "Tipo", "Espera (s)"]],
            **estilo_tabla
        )
    else:
        tabla_bloqueos = dbc.Alert("✅ Sin bloqueos activos detectados.", color="success", className="py-2 mt-2")

    # Índices no usados
    indices = diagnostico.get("indices_no_usados", [])
    if indices:
        tabla_indices = dash_table.DataTable(
            data=[{"Esquema": i["esquema"], "Tabla": i["tabla"], "Índice": i["nombre_indice"],
                   "Tamaño (MB)": i["tamanio_mb"], "Escaneos": i["escaneos_indice"]} for i in indices],
            columns=[{"name": col, "id": col} for col in ["Esquema", "Tabla", "Índice", "Tamaño (MB)", "Escaneos"]],
            **estilo_tabla
        )
    else:
        tabla_indices = dbc.Alert("✅ Sin índices no utilizados detectados.", color="success", className="py-2 mt-2")

    return tabla_consultas, tabla_bloqueos, tabla_indices


# ─── Entrada ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)

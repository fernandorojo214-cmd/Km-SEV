import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import pytz
import os
import re
import base64
import io
import urllib.parse
import cloudinary
import cloudinary.uploader
import cloudinary.api
import streamlit_authenticator as stauth
import bcrypt
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter
from streamlit_gsheets import GSheetsConnection
from streamlit_autorefresh import st_autorefresh

# =========================================================
# CONFIGURACIÓN (TODO DESDE SECRETS, NADA HARDCODEADO)
# =========================================================
# Ver secrets.toml.example para el formato completo esperado.
# Requiere en requirements.txt la MISMA versión de streamlit-authenticator
# que tengas instalada localmente (ver diagnostico_auth.py). Este código
# usa la API de la rama 0.4.x (login basado en st.session_state, sin la
# clase Hasher — el hash de contraseñas se hace con bcrypt directamente).

cloudinary.config(
    cloud_name=st.secrets["cloudinary"]["cloud_name"],
    api_key=st.secrets["cloudinary"]["api_key"],
    api_secret=st.secrets["cloudinary"]["api_secret"],
    secure=True,
)

st.set_page_config(page_title="Control de Flotilla", layout="centered")

COLUMNAS_ESPERADAS = [
    'Fecha', 'Nombre', 'Kilometraje Inicial', 'Kilometraje Final',
    'Total Recorrido', 'Carga del Día', 'Lugar de Carga', 'Comentarios',
    'Comprobante'
]
COLUMNAS_USUARIOS = ['Nombre', 'Username', 'Password', 'Rol', 'Activo']

# --- FUNCIONES DE APOYO ---
def subir_archivo_a_nube(file_obj):
    try:
        es_pdf = file_obj.name.lower().endswith('.pdf') if hasattr(file_obj, "name") else False
        tipo_recurso = "raw" if es_pdf else "auto"
        resultado = cloudinary.uploader.upload(
            file_obj, resource_type=tipo_recurso, use_filename=True, unique_filename=True,
        )
        return resultado['secure_url']
    except Exception as e:
        st.error(f"Error al subir archivo: {e}")
        return None


def extraer_datos_cloudinary(url):
    res_type = "raw" if "/raw/" in url else "image"
    archivo = url.split('/')[-1]
    public_id = archivo if res_type == "raw" else archivo.rsplit('.', 1)[0]
    return public_id, res_type


def calcular_total_carga(texto):
    numeros = re.findall(r"[-+]?\d*\.\d+|\d+", texto or "")
    return sum(float(n) for n in numeros) if numeros else 0.0


def asegurar_columnas(df, columnas):
    for col in columnas:
        if col not in df.columns:
            df[col] = None
    return df


def horas_desde(fecha_str):
    try:
        zona_cdmx = pytz.timezone('America/Mexico_City')
        f = pd.to_datetime(fecha_str)
        if pd.isna(f):
            return None  # Fecha vacía/inválida (NaT) — no truena, simplemente no hay dato
        if f.tzinfo is None:
            f = zona_cdmx.localize(f)
        resultado = (datetime.now(zona_cdmx) - f).total_seconds() / 3600
        return None if pd.isna(resultado) else resultado
    except Exception:
        return None


COLUMNAS_ESTACIONES = ['Nombre', 'Red', 'Direccion', 'Notas', 'Lat', 'Lng']

# Datos de partida por si aún no creas la pestaña "Estaciones" en tu Google
# Sheet, o mientras la llenas. Direcciones y coordenadas reales de hubs
# conocidos de VEMO y de la red pública de CFE en CDMX — agrega/edita las
# que uses tú directamente en la pestaña "Estaciones" del Sheet.
ESTACIONES_SEMILLA = [
    {"Nombre": "VEMO HUB San Pedro de los Pinos", "Red": "VEMO",
     "Direccion": "F.C. de Cuernavaca 1454, San Pedro de los Pinos, 01180, CDMX", "Notas": "44 cargadores",
     "Lat": 19.3902134, "Lng": -99.1905603},
    {"Nombre": "Artz Pedregal", "Red": "CFE",
     "Direccion": "Periférico Sur 3720, Jardines del Pedregal, 01900, CDMX", "Notas": "",
     "Lat": 19.3135357, "Lng": -99.2192939},
    {"Nombre": "German Center Santa Fe", "Red": "CFE",
     "Direccion": "Av. Santa Fe 170, Zedec Santa Fe, 01219, CDMX", "Notas": "Cargadores Tesla Destination",
     "Lat": 19.3666775, "Lng": -99.2607456},
    {"Nombre": "Town Center El Rosario", "Red": "CFE",
     "Direccion": "Av. Río Blanco 69, El Rosario, Azcapotzalco, 02100, CDMX", "Notas": "",
     "Lat": 19.5036045, "Lng": -99.2036643},
    {"Nombre": "IPADE Clavería", "Red": "CFE",
     "Direccion": "Calle Floresta 20, Clavería, Azcapotzalco, 02080, CDMX", "Notas": "",
     "Lat": 19.4639424, "Lng": -99.1871372},
]


@st.cache_data(ttl=300)
def leer_estaciones(_conn):
    """Lee la pestaña 'Estaciones' del Google Sheet. Si aún no existe,
    usa la lista semilla para que la pestaña no se vea vacía desde el
    primer día — crea la pestaña 'Estaciones' en tu Sheet (columnas
    Nombre, Red, Direccion, Notas, Lat, Lng) para reemplazar/ampliar esta lista."""
    try:
        df = _conn.read(worksheet="Estaciones", ttl=300)
        df.columns = [str(c).strip() for c in df.columns]
        df = asegurar_columnas(df, COLUMNAS_ESTACIONES)
        df = df.dropna(subset=['Nombre'])
        if df.empty:
            return pd.DataFrame(ESTACIONES_SEMILLA)
        return df
    except Exception:
        return pd.DataFrame(ESTACIONES_SEMILLA)


def link_como_llegar(direccion: str) -> str:
    """Arma el enlace público de direcciones de Google Maps a partir de
    una dirección en texto — no necesita API key ni coordenadas exactas."""
    return f"https://www.google.com/maps/dir/?api=1&destination={urllib.parse.quote(direccion)}"


def esta_activo(valor):
    """Interpreta la columna 'Activo' de forma flexible.
    Google Sheets puede guardar TRUE/FALSE como texto, como booleano,
    o —si detecta automáticamente una casilla de verificación— como
    número (1.0 / 0.0), por eso hay que cubrir todos los casos:
    TRUE/Sí/1/1.0/Activo = activo, FALSE/No/0/0.0/Inactivo = inactivo.
    Si la celda está vacía (filas viejas antes de agregar esta columna),
    se considera activo por defecto para no bloquear a nadie sin querer."""
    if valor is None:
        return True
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        if isinstance(valor, float) and pd.isna(valor):
            return True
        return valor != 0

    texto = str(valor).strip().lower()
    if texto in ("", "nan", "none"):
        return True
    if texto in ("false", "falso", "no", "inactivo", "0"):
        return False
    if texto in ("true", "verdadero", "si", "sí", "activo", "1"):
        return True
    # Cubre casos como "1.0" o "0.0" que llegan como texto
    try:
        return float(texto) != 0
    except ValueError:
        return True  # si no se reconoce el formato, no bloqueamos por defecto


@st.cache_data(ttl=60)
def leer_usuarios(_conn, version=0):
    """Lectura centralizada y cacheada de la pestaña 'Usuarios'.
    El parámetro 'version' se usa como "cache-buster": cada vez que se
    guarda un cambio (alta, baja, reset de contraseña) se incrementa
    en session_state, forzando una lectura nueva en vez de confiar
    únicamente en que st.cache_data.clear() alcance a todo.
    Antes cada sección del Dashboard leía la hoja por su cuenta con
    ttl=0 (sin caché), lo que disparaba demasiadas solicitudes a la
    API de Google Sheets y provocaba errores de límite de solicitudes
    (APIError / rate limit). Ahora todo pasa por aquí."""
    df = _conn.read(worksheet="Usuarios", ttl=60)
    df.columns = [str(c).strip() for c in df.columns]
    df = asegurar_columnas(df, COLUMNAS_USUARIOS)
    # Forzamos tipo texto en estas columnas: si venían vacías en Google
    # Sheets, pandas las infiere como numéricas (float64) y truena al
    # intentar meterles texto como "TRUE"/"FALSE" o un hash de contraseña.
    for col in COLUMNAS_USUARIOS:
        df[col] = df[col].astype("object")
    return df


def leer_usuarios_fresco(conn):
    """Lectura SIN caché de 'Usuarios' — se usa solo en las acciones de
    administración (dar de alta/baja, resetear contraseña), que son poco
    frecuentes, para garantizar que SIEMPRE se vea el dato más reciente
    de Google Sheets sin depender de ningún mecanismo de caché."""
    df = conn.read(worksheet="Usuarios", ttl=0)
    df.columns = [str(c).strip() for c in df.columns]
    df = asegurar_columnas(df, COLUMNAS_USUARIOS)
    for col in COLUMNAS_USUARIOS:
        df[col] = df[col].astype("object")
    return df


def version_usuarios():
    """Contador que forzamos a subir cada vez que se guarda un cambio
    en Usuarios, para invalidar el caché de forma explícita y confiable."""
    if "usuarios_version" not in st.session_state:
        st.session_state["usuarios_version"] = 0
    return st.session_state["usuarios_version"]


def invalidar_cache_usuarios():
    st.session_state["usuarios_version"] = version_usuarios() + 1
    st.cache_data.clear()


@st.cache_data(ttl=60)
def cargar_credenciales(_conn):
    """Lee la hoja 'Usuarios' y arma el diccionario que necesita
    streamlit-authenticator: {usernames: {user: {name, password, role}}}"""
    df = leer_usuarios(_conn, version_usuarios())
    df = df.dropna(subset=['Username'])

    credenciales = {"usernames": {}}
    for _, fila in df.iterrows():
        username = str(fila['Username']).strip()
        if not username:
            continue
        if not esta_activo(fila.get('Activo')):
            continue  # conductor dado de baja: no puede iniciar sesión
        credenciales["usernames"][username] = {
            "name": str(fila['Nombre']).strip(),
            "password": str(fila['Password']).strip(),  # ya viene hasheado
            "role": str(fila.get('Rol', 'conductor')).strip().lower() or "conductor",
            # streamlit-authenticator 0.4.x espera este campo aunque no
            # usemos recuperación de contraseña por correo; lo dejamos vacío.
            "email": "",
        }
    return credenciales


def _mensaje_error_amigable(e):
    """Traduce errores técnicos comunes de Google Sheets a algo entendible."""
    texto = str(e).lower()
    if "429" in texto or "quota" in texto or "rate" in texto:
        return (
            "⏳ Google Sheets está recibiendo demasiadas solicitudes en poco tiempo. "
            "Espera unos 30-60 segundos y vuelve a intentar."
        )
    if "403" in texto or "permission" in texto:
        return (
            "🔒 La cuenta de servicio no tiene permiso de edición sobre el Google Sheet. "
            "Verifica que esté compartida con permiso de 'Editor'."
        )
    return f"❌ Error técnico al guardar: {type(e).__name__}: {e}"


def agregar_usuario(conn, nombre, username, password_plano, rol):
    """Usado por el admin para dar de alta un nuevo conductor.
    Hashea la contraseña antes de guardarla — nunca se guarda en texto plano."""
    try:
        df = leer_usuarios_fresco(conn)

        if username in df['Username'].astype(str).str.strip().values:
            return False, "Ese username ya existe. Elige otro."

        hash_pw = bcrypt.hashpw(password_plano.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        nueva_fila = {"Nombre": nombre, "Username": username, "Password": hash_pw, "Rol": rol, "Activo": "Activo"}
        df = pd.concat([df, pd.DataFrame([nueva_fila])], ignore_index=True)

        # Solo escribimos las 4 columnas que nos interesan, en orden fijo,
        # para no arrastrar columnas viejas/duplicadas a la hoja.
        df = df[COLUMNAS_USUARIOS]

        conn.update(worksheet="Usuarios", data=df)
        invalidar_cache_usuarios()
        return True, "Conductor agregado correctamente."
    except Exception as e:
        return False, _mensaje_error_amigable(e)


def cambiar_estado_usuario(conn, username, activar: bool):
    """Da de baja (o reactiva) a un conductor sin borrar su historial.
    Simplemente le apaga el acceso cambiando la columna 'Activo'."""
    try:
        df = leer_usuarios_fresco(conn)

        username_buscado = username.strip().lower()
        mascara = df['Username'].astype(str).str.strip().str.lower() == username_buscado

        if not mascara.any():
            return False, "No se encontró ese conductor."

        df.loc[mascara, 'Activo'] = "Activo" if activar else "Inactivo"
        df = df[COLUMNAS_USUARIOS]
        conn.update(worksheet="Usuarios", data=df)
        invalidar_cache_usuarios()

        accion = "reactivado" if activar else "dado de baja"
        return True, f"Conductor {accion} correctamente."
    except Exception as e:
        return False, _mensaje_error_amigable(e)


def resetear_password(conn, username, nueva_password_plano):
    """Permite al admin poner una contraseña temporal nueva a un conductor
    que la olvidó, sin necesidad de tocar el Google Sheet a mano."""
    try:
        df = leer_usuarios_fresco(conn)

        username_buscado = username.strip().lower()
        mascara = df['Username'].astype(str).str.strip().str.lower() == username_buscado

        if not mascara.any():
            return False, "No se encontró ese conductor."

        hash_pw = bcrypt.hashpw(nueva_password_plano.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        df.loc[mascara, 'Password'] = hash_pw
        df = df[COLUMNAS_USUARIOS]
        conn.update(worksheet="Usuarios", data=df)
        invalidar_cache_usuarios()
        return True, "Contraseña restablecida correctamente. Comparte la nueva contraseña con el conductor."
    except Exception as e:
        return False, _mensaje_error_amigable(e)


# --- ESTILOS Y MARCA (SEV) ---
def inyectar_estilos():
    """Tema visual de SEV: paleta ember/amber (energía y carga eléctrica),
    tipografía Space Grotesk + Inter, tarjetas con acento lateral en vez
    del típico kit de tarjetas redondeadas con sombra genérica."""
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&display=swap');

    :root {
        --sev-ink: #1B1F24;
        --sev-surface: #F2F3F5;
        --sev-ember: #E8491D;
        --sev-amber: #FFB020;
        --sev-success: #1F9D55;
        --sev-danger: #C81E3A;
    }

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    h1, h2, h3, .sev-banner-title {
        font-family: 'Space Grotesk', sans-serif !important;
        letter-spacing: -0.01em;
    }

    /* Banner de marca */
    .sev-banner {
        background: var(--sev-ink);
        border-radius: 16px;
        padding: 26px 30px 0 30px;
        margin-bottom: 24px;
        overflow: hidden;
    }
    .sev-banner-row {
        display: flex;
        align-items: center;
        gap: 18px;
        padding-bottom: 20px;
    }
    .sev-banner-title {
        color: #FAFAF8;
        font-size: 1.8rem;
        font-weight: 700;
        margin: 0;
        line-height: 1.15;
    }
    .sev-banner-subtitle {
        color: #B9BEC7;
        font-size: 0.92rem;
        margin: 4px 0 0 0;
    }
    .sev-charge-bar {
        height: 6px;
        width: 100%;
        background: linear-gradient(90deg, var(--sev-ember), var(--sev-amber));
    }

    /* Pestañas como control segmentado, no como pastillas */
    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        border-bottom: 2px solid var(--sev-surface);
    }
    .stTabs [data-baseweb="tab"] {
        font-family: 'Space Grotesk', sans-serif;
        font-weight: 600;
        color: #6B7280;
    }
    .stTabs [aria-selected="true"] {
        color: var(--sev-ink) !important;
        border-bottom: 3px solid var(--sev-ember) !important;
    }

    /* Botones */
    .stButton>button, .stFormSubmitButton>button, .stDownloadButton>button {
        border-radius: 10px;
        font-weight: 600;
    }
    .stButton>button[kind="primary"],
    .stFormSubmitButton>button[kind="primary"],
    .stDownloadButton>button[kind="primary"] {
        background-color: var(--sev-ember);
        border-color: var(--sev-ember);
    }
    .stButton>button[kind="primary"]:hover,
    .stFormSubmitButton>button[kind="primary"]:hover {
        background-color: #C93E17;
        border-color: #C93E17;
    }

    /* Tarjetas de métricas: acento lateral, sin sombra genérica */
    div[data-testid="stMetric"] {
        background: var(--sev-surface);
        border-left: 4px solid var(--sev-ember);
        border-radius: 6px;
        padding: 14px 16px;
    }

    /* Estados como pastilla de color, para escanear rápido */
    .sev-pill {
        display: inline-block;
        padding: 3px 14px;
        border-radius: 999px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .sev-pill-activo { background: rgba(31, 157, 85, 0.12); color: var(--sev-success); }
    .sev-pill-inactivo { background: rgba(200, 30, 58, 0.1); color: var(--sev-danger); }
    .sev-pill-amarillo { background: rgba(255, 176, 32, 0.16); color: #B8790A; }

    /* Tarjeta de estación de carga + botón "Cómo llegar" */
    .sev-estacion-card {
        background: var(--sev-surface);
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 12px;
    }
    .sev-estacion-nombre { font-weight: 700; font-size: 1.05rem; margin: 0; }
    .sev-estacion-direccion { color: #4B5563; font-size: 0.9rem; margin: 4px 0 10px 0; }
    .sev-pill-red {
        display: inline-block;
        padding: 2px 12px;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 700;
        background: rgba(232, 73, 29, 0.12);
        color: var(--sev-ember);
        margin-bottom: 6px;
    }
    .sev-btn-llegar {
        display: inline-block;
        padding: 7px 16px;
        background: var(--sev-ember);
        color: #FFFFFF !important;
        border-radius: 8px;
        text-decoration: none !important;
        font-weight: 600;
        font-size: 0.88rem;
    }
    .sev-btn-llegar:hover { background: #C93E17; }
    </style>
    """, unsafe_allow_html=True)


def badge_estado(activo: bool) -> str:
    """HTML de la pastilla de estado (Activo/Dado de baja) para usar con st.markdown."""
    if activo:
        return '<span class="sev-pill sev-pill-activo">Activo</span>'
    return '<span class="sev-pill sev-pill-inactivo">Dado de baja</span>'


# --- ENCABEZADO ---
inyectar_estilos()

logo_html = ""
if os.path.exists("logo.png"):
    with open("logo.png", "rb") as _f:
        _logo_b64 = base64.b64encode(_f.read()).decode()
    logo_html = f'<img src="data:image/png;base64,{_logo_b64}" style="height:50px;border-radius:8px;">'

st.markdown(f"""
<div class="sev-banner">
  <div class="sev-banner-row">
    {logo_html}
    <div>
      <p class="sev-banner-title">Control de Flotilla</p>
      <p class="sev-banner-subtitle">Flotilla eléctrica SEV — turnos, kilometraje y carga</p>
    </div>
  </div>
  <div class="sev-charge-bar"></div>
</div>
""", unsafe_allow_html=True)

conn = st.connection("gsheets", type=GSheetsConnection)
zona_cdmx = pytz.timezone('America/Mexico_City')

# =========================================================
# LOGIN
# =========================================================
try:
    credenciales = cargar_credenciales(conn)
except Exception as e:
    st.error(_mensaje_error_amigable(e))
    if st.button("🔄 Reintentar"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

authenticator = stauth.Authenticate(
    credenciales,
    st.secrets["cookie"]["name"],
    st.secrets["cookie"]["key"],
    st.secrets["cookie"]["expiry_days"],
)

# streamlit-authenticator 0.4.x ya no regresa una tupla: guarda todo
# en st.session_state (authentication_status, name, username).
authenticator.login(
    location="main",
    fields={
        "Form name": "Iniciar Sesión",
        "Username": "Usuario",
        "Password": "Contraseña",
        "Login": "Entrar",
    },
)

estado_auth = st.session_state.get("authentication_status")
nombre_usuario = st.session_state.get("name")
username = st.session_state.get("username")

if estado_auth is False:
    st.error("❌ Usuario o contraseña incorrectos.")
    st.stop()
elif estado_auth is None:
    st.info("👋 Ingresa tu usuario y contraseña para continuar.")
    st.stop()

# --- A partir de aquí, el usuario ya está autenticado ---
rol_usuario = credenciales["usernames"][username].get("role", "conductor")
es_admin = rol_usuario == "admin"

with st.sidebar:
    st.write(f"👤 Sesión: **{nombre_usuario}**")
    st.caption(f"Rol: {rol_usuario}")
    authenticator.logout("Cerrar sesión", "sidebar")

# --- PESTAÑAS SEGÚN ROL ---
nombres_tabs = ["🟢 Iniciar Turno", "🔴 Finalizar Turno", "📋 Mi Historial", "🔌 Estaciones de Carga"]
if es_admin:
    nombres_tabs.append("🟢 En Vivo")
    nombres_tabs.append("📊 Dashboard Admin")

tabs = st.tabs(nombres_tabs)
tab_inicio, tab_fin, tab_historial, tab_estaciones = tabs[0], tabs[1], tabs[2], tabs[3]
if es_admin:
    tab_en_vivo = tabs[4]
    tab_dash = tabs[5]

# El nombre ya no se pide ni se selecciona: viene de la sesión autenticada.
# Esto elimina por completo el riesgo de escribir mal el nombre o
# seleccionar el de otro compañero.
nombre_actual = nombre_usuario

# --- PESTAÑA 1: INICIO DE TURNO ---
with tab_inicio:
    st.header(f"Registro de Inicio — {nombre_actual}")
    km_inicio = st.number_input(
        "Kilometraje Inicial", min_value=0.0, step=0.1, value=None,
        placeholder="Ej. 12500", key="km_ini"
    )

    if st.button("Registrar Inicio de Turno", type="primary", use_container_width=True):
        if km_inicio is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            df_actualizado = asegurar_columnas(df_actualizado, COLUMNAS_ESPERADAS)
            for col in ['Carga del Día', 'Lugar de Carga', 'Comentarios', 'Comprobante']:
                df_actualizado[col] = df_actualizado[col].astype("object")

            nombre_buscado_ini = nombre_actual.strip().lower()
            turno_abierto = df_actualizado[
                (df_actualizado['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado_ini) &
                (pd.isna(df_actualizado['Kilometraje Final']) | (df_actualizado['Kilometraje Final'] == ""))
            ]

            if not turno_abierto.empty:
                st.error(f"⚠️ Ya tienes un turno iniciado, {nombre_actual}. Ve a 'Finalizar Turno' primero.")
            else:
                ahora_cdmx = datetime.now(zona_cdmx).strftime("%Y-%m-%d %H:%M:%S")
                nuevo_registro = {
                    'Fecha': ahora_cdmx, 'Nombre': nombre_actual, 'Kilometraje Inicial': float(km_inicio),
                    'Kilometraje Final': None, 'Total Recorrido': None, 'Carga del Día': None,
                    'Lugar de Carga': None, 'Comentarios': None, 'Comprobante': None
                }
                df_actualizado = pd.concat([df_actualizado, pd.DataFrame([nuevo_registro])], ignore_index=True)
                conn.update(worksheet="Hoja 1", data=df_actualizado)
                st.cache_data.clear()
                st.success(f"✅ ¡Buen viaje, {nombre_actual}!")
                st.balloons()
        else:
            st.warning("⚠️ Ingresa tu kilometraje inicial.")

# --- PESTAÑA 2: FIN DE TURNO ---
with tab_fin:
    st.header(f"Registro Final — {nombre_actual}")
    km_fin = st.number_input(
        "Kilometraje Final", min_value=0.0, step=0.1, value=None,
        placeholder="Ej. 12650", key="km_fin"
    )
    carga_dia = st.text_input("Carga del Día ($)", placeholder="Ej: 123 o 500 + 200", key="carga_dia")

    df_lugares_hist = conn.read(worksheet="Hoja 1", ttl=60)
    lugares_frecuentes = []
    if not df_lugares_hist.empty and 'Lugar de Carga' in df_lugares_hist.columns:
        lg = df_lugares_hist['Lugar de Carga'].dropna().astype(str).str.strip().str.title()
        lg = lg[~lg.isin(["N/A", "None", "", "Nan"])]
        if not lg.empty:
            lugares_frecuentes = lg.value_counts().head(8).index.tolist()

    if lugares_frecuentes:
        opciones_lugar = lugares_frecuentes + ["➕ Otro lugar (escribir)"]
        lugar_sel = st.selectbox("Lugar de Carga", opciones_lugar, key="lugar_sel")
        lugar_carga = st.text_input("Escribe el lugar de carga", key="lugar_carga_manual") \
            if lugar_sel == "➕ Otro lugar (escribir)" else lugar_sel
    else:
        lugar_carga = st.text_input("Lugar de Carga", key="lugar_carga")

    with st.expander("➕ Comentarios (opcional)"):
        txt_comentarios = st.text_area("Comentarios", key="coment", label_visibility="collapsed")

    st.write("**Comprobante del ticket**")
    metodo_foto = st.radio(
        "¿Cómo quieres subir el comprobante?",
        ["📁 Subir archivo (foto o PDF)", "📷 Tomar foto ahora"],
        horizontal=True, key="metodo_foto"
    )
    archivos_tickets = []
    if metodo_foto == "📷 Tomar foto ahora":
        foto = st.camera_input("Toma una foto del ticket", key="camera_ticket")
        if foto is not None:
            archivos_tickets = [foto]
    else:
        archivos_tickets = st.file_uploader(
            "Subir fotos o PDFs de los Tickets", type=["png", "jpg", "jpeg", "pdf"],
            accept_multiple_files=True, key="uploader_ticket"
        ) or []

    if st.button("Registrar Fin de Turno", type="primary", use_container_width=True):
        if km_fin is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            df_actualizado = asegurar_columnas(df_actualizado, COLUMNAS_ESPERADAS)
            for col in ['Carga del Día', 'Lugar de Carga', 'Comentarios', 'Comprobante']:
                df_actualizado[col] = df_actualizado[col].astype("object")

            nombre_buscado = nombre_actual.strip().lower()
            pendientes = df_actualizado[
                (df_actualizado['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado) &
                (pd.isna(df_actualizado['Kilometraje Final']) | (df_actualizado['Kilometraje Final'] == ""))
            ]

            if not pendientes.empty:
                idx = pendientes.index[-1]
                km_ini = float(df_actualizado.at[idx, 'Kilometraje Inicial'])

                if km_fin >= km_ini:
                    total_dinero = calcular_total_carga(carga_dia)
                    links_archivos = []
                    if archivos_tickets:
                        with st.spinner("Subiendo archivos a la nube..."):
                            for archivo in archivos_tickets:
                                url = subir_archivo_a_nube(archivo)
                                if url:
                                    links_archivos.append(url)

                    link_final = " ".join(links_archivos) if links_archivos else "No subido"
                    total_recorrido = float(km_fin - km_ini)

                    df_actualizado.at[idx, 'Kilometraje Final'] = float(km_fin)
                    df_actualizado.at[idx, 'Total Recorrido'] = total_recorrido
                    df_actualizado.at[idx, 'Carga del Día'] = total_dinero
                    df_actualizado.at[idx, 'Lugar de Carga'] = str(lugar_carga) if lugar_carga else "N/A"
                    df_actualizado.at[idx, 'Comentarios'] = str(txt_comentarios) if txt_comentarios else ""
                    df_actualizado.at[idx, 'Comprobante'] = link_final

                    conn.update(worksheet="Hoja 1", data=df_actualizado)
                    st.cache_data.clear()

                    st.success(f"🏁 ¡Turno finalizado con éxito, {nombre_actual}!")
                    st.success(f"🚖 Km Recorridos: {total_recorrido} km | 🔋 Carga: ${total_dinero}")
                    st.balloons()
                else:
                    st.error(f"❌ El kilometraje final ({km_fin}) no puede ser menor al inicial ({km_ini}).")
            else:
                st.error("❌ No se encontró un turno activo. Verifica que hayas iniciado turno primero.")
        else:
            st.warning("⚠️ Completa tu kilometraje final.")

# --- PESTAÑA 3: MI HISTORIAL ---
with tab_historial:
    st.header(f"Historial de {nombre_actual}")
    df_hist = conn.read(worksheet="Hoja 1", ttl=30)
    df_hist = asegurar_columnas(df_hist, COLUMNAS_ESPERADAS)
    nombre_buscado_hist = nombre_actual.strip().lower()
    propios = df_hist[df_hist['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado_hist].copy()

    if propios.empty:
        st.info("Aún no tienes turnos registrados.")
    else:
        propios['Fecha'] = pd.to_datetime(propios['Fecha'], errors='coerce')
        propios = propios.sort_values('Fecha', ascending=False)

        # Aviso de turno abierto (se calcula sobre TODO el historial,
        # sin importar el filtro de fechas, para no ocultarlo por error)
        turno_pendiente = propios[pd.isna(propios['Kilometraje Final']) | (propios['Kilometraje Final'] == "")]
        if not turno_pendiente.empty:
            fila = turno_pendiente.iloc[0]
            horas = horas_desde(fila['Fecha'])
            aviso = f"🟡 Tienes un turno abierto desde {fila['Fecha']}"
            if horas is not None:
                aviso += f" (hace {horas:.1f} horas)"
            (st.warning if (horas or 0) > 12 else st.info)(aviso)

        # --- Filtro por rango de fechas (igual estilo que el reporte semanal) ---
        fechas_validas = propios['Fecha'].dropna()
        fecha_min_disponible = fechas_validas.min().date() if not fechas_validas.empty else datetime.now(zona_cdmx).date()
        fecha_max_disponible = fechas_validas.max().date() if not fechas_validas.empty else datetime.now(zona_cdmx).date()

        col_fi, col_ff = st.columns(2)
        with col_fi:
            fecha_inicio_hist = st.date_input(
                "Desde:", value=fecha_min_disponible,
                min_value=fecha_min_disponible, max_value=fecha_max_disponible,
                key="hist_fecha_inicio"
            )
        with col_ff:
            fecha_fin_hist = st.date_input(
                "Hasta:", value=fecha_max_disponible,
                min_value=fecha_min_disponible, max_value=fecha_max_disponible,
                key="hist_fecha_fin"
            )

        if fecha_inicio_hist > fecha_fin_hist:
            st.error("❌ La fecha 'Desde' no puede ser posterior a la fecha 'Hasta'.")
            propios_filtrado = propios.iloc[0:0]  # tabla vacía
        else:
            propios_filtrado = propios[
                (propios['Fecha'].dt.date >= fecha_inicio_hist) &
                (propios['Fecha'].dt.date <= fecha_fin_hist)
            ]

        total_km = pd.to_numeric(propios_filtrado['Total Recorrido'], errors='coerce').fillna(0).sum()
        total_gasto = pd.to_numeric(propios_filtrado['Carga del Día'], errors='coerce').fillna(0).sum()

        m1, m2, m3 = st.columns(3)
        m1.metric("Turnos registrados", len(propios_filtrado))
        m2.metric("KM totales", f"{total_km:,.1f} km")
        m3.metric("Gasto total en carga", f"${total_gasto:,.2f}")

        st.divider()
        if propios_filtrado.empty:
            st.info("No hay turnos registrados en el rango de fechas seleccionado.")
        else:
            st.dataframe(
                propios_filtrado[['Fecha', 'Kilometraje Inicial', 'Kilometraje Final', 'Total Recorrido',
                                   'Carga del Día', 'Lugar de Carga', 'Comentarios']],
                use_container_width=True, hide_index=True
            )

# --- PESTAÑA: ESTACIONES DE CARGA ---
with tab_estaciones:
    st.header("Estaciones de Carga")
    st.caption(
        "Ubicaciones de referencia para cargar durante tu turno. Da clic en "
        "'Cómo llegar' para abrir la ruta directo en Google Maps."
    )

    df_estaciones = leer_estaciones(conn)

    redes_disponibles = sorted(df_estaciones['Red'].dropna().astype(str).str.strip().unique())
    if len(redes_disponibles) > 1:
        filtro_red = st.selectbox("Filtrar por red:", ["Todas"] + redes_disponibles)
        if filtro_red != "Todas":
            df_estaciones = df_estaciones[df_estaciones['Red'].astype(str).str.strip() == filtro_red]

    if df_estaciones.empty:
        st.info("Aún no hay estaciones registradas.")
    else:
        # --- Mapa con pines, solo para las estaciones que sí tienen Lat/Lng ---
        mapa_df = df_estaciones.copy()
        mapa_df['Lat'] = pd.to_numeric(mapa_df.get('Lat'), errors='coerce')
        mapa_df['Lng'] = pd.to_numeric(mapa_df.get('Lng'), errors='coerce')
        mapa_df = mapa_df.dropna(subset=['Lat', 'Lng'])
        mapa_df = mapa_df[(mapa_df['Lat'] != 0) & (mapa_df['Lng'] != 0)]

        if not mapa_df.empty:
            st.map(mapa_df.rename(columns={'Lat': 'lat', 'Lng': 'lon'})[['lat', 'lon']], size=60, color="#E8491D")
            faltan = len(df_estaciones) - len(mapa_df)
            if faltan > 0:
                st.caption(
                    f"📍 Mostrando {len(mapa_df)} de {len(df_estaciones)} estaciones en el mapa "
                    f"({faltan} sin coordenadas todavía — agrega Lat/Lng en el Sheet para que aparezcan)."
                )
        else:
            st.caption(
                "💡 Agrega columnas 'Lat' y 'Lng' en tu pestaña 'Estaciones' para ver el mapa con pines "
                "(clic derecho en el punto exacto dentro de Google Maps → copiar las coordenadas)."
            )

        st.divider()
        for _, fila in df_estaciones.iterrows():
            nombre = str(fila.get('Nombre', '')).strip()
            red = str(fila.get('Red', '')).strip()
            direccion = str(fila.get('Direccion', '')).strip()
            notas = str(fila.get('Notas', '')).strip()
            if not nombre:
                continue

            link = link_como_llegar(direccion if direccion else nombre)
            tiene_notas = notas and notas.lower() != "nan"
            tiene_red = red and red.lower() != "nan"
            notas_html = f'<p class="sev-estacion-direccion">Nota: {notas}</p>' if tiene_notas else ""
            red_html = f'<span class="sev-pill-red">{red}</span><br>' if tiene_red else ""

            tarjeta_html = (
                '<div class="sev-estacion-card">'
                + red_html
                + f'<p class="sev-estacion-nombre">{nombre}</p>'
                + f'<p class="sev-estacion-direccion">Direccion: {direccion}</p>'
                + notas_html
                + f'<a href="{link}" target="_blank" class="sev-btn-llegar">Como llegar</a>'
                + '</div>'
            )
            st.markdown(tarjeta_html, unsafe_allow_html=True)

    if es_admin:
        st.divider()
        st.caption(
            "Como admin: para agregar, editar o quitar estaciones, edita la pestaña "
            "'Estaciones' de tu Google Sheet (columnas: Nombre, Red, Direccion, Notas, Lat, Lng). "
            "Lat/Lng son opcionales (solo hacen falta para que aparezca el pin en el mapa) — "
            "se consiguen dando clic derecho sobre el punto exacto en Google Maps y copiando las "
            "coordenadas. Si esa pestaña no existe todavia, creala -- mientras tanto se muestra "
            "una lista de ejemplo."
        )

# --- PESTAÑA: EN VIVO (SOLO ADMIN) ---
if es_admin:
    with tab_en_vivo:
        st.header("🟢 Conductores en Turno Ahora")
        st.caption(
            "Quién está activo en este momento y cuántas horas lleva desde que "
            "inició turno. Esta pestaña se actualiza sola cada minuto."
        )

        # La página se vuelve a ejecutar sola cada 60 segundos gracias a
        # st_autorefresh, así que las horas suben solas en pantalla sin que
        # el admin tenga que refrescar manualmente. Se usa ttl=30 (no
        # ttl=0) para no golpear la API de Google Sheets con una solicitud
        # nueva cada minuto sin necesidad.
        st_autorefresh(interval=60_000, key="autorefresh_en_vivo")

        df_en_vivo = conn.read(worksheet="Hoja 1", ttl=30)
        df_en_vivo = asegurar_columnas(df_en_vivo, COLUMNAS_ESPERADAS)
        df_en_vivo = df_en_vivo[
            pd.isna(df_en_vivo['Kilometraje Final']) | (df_en_vivo['Kilometraje Final'] == "")
        ].copy()
        df_en_vivo['Fecha'] = pd.to_datetime(df_en_vivo['Fecha'], errors='coerce')

        # Solo turnos abiertos DENTRO de la semana actual (lunes-domingo,
        # hora CDMX) cuentan como "activos ahora". Un turno que quedó
        # abierto desde hace semanas o meses casi siempre es un error de
        # captura (el conductor olvidó dar "Finalizar Turno"), no un turno
        # real en curso, así que no debe mezclarse aquí ni inflar la lista.
        hoy_cdmx = datetime.now(zona_cdmx).date()
        inicio_semana_actual = hoy_cdmx - timedelta(days=hoy_cdmx.weekday())

        df_antiguos = df_en_vivo[
            df_en_vivo['Fecha'].notna() & (df_en_vivo['Fecha'].dt.date < inicio_semana_actual)
        ].copy()
        df_en_vivo = df_en_vivo[
            df_en_vivo['Fecha'].isna() | (df_en_vivo['Fecha'].dt.date >= inicio_semana_actual)
        ].copy()

        if df_en_vivo.empty:
            st.info("No hay conductores en turno esta semana.")
        else:
            df_en_vivo['Horas Activo'] = df_en_vivo['Fecha'].apply(horas_desde)
            df_en_vivo = df_en_vivo.sort_values('Horas Activo', ascending=False, na_position='last')

            st.metric("Conductores activos esta semana", len(df_en_vivo))
            st.divider()

            for _, fila in df_en_vivo.iterrows():
                nombre_activo = str(fila.get('Nombre', '')).strip()
                fecha_inicio_turno = fila.get('Fecha', '')
                km_inicio_turno = fila.get('Kilometraje Inicial', '')
                horas_turno = fila.get('Horas Activo')

                if horas_turno is None or pd.isna(horas_turno):
                    texto_horas = "N/D"
                    clase_pill = "sev-pill-activo"
                else:
                    texto_horas = f"{horas_turno:.1f} h"
                    if horas_turno < 8:
                        clase_pill = "sev-pill-activo"
                    elif horas_turno < 12:
                        clase_pill = "sev-pill-amarillo"
                    else:
                        clase_pill = "sev-pill-inactivo"

                tarjeta_en_vivo_html = (
                    '<div class="sev-estacion-card">'
                    f'<p class="sev-estacion-nombre">{nombre_activo} '
                    f'<span class="sev-pill {clase_pill}">⏱ {texto_horas}</span></p>'
                    f'<p class="sev-estacion-direccion">Inicio de turno: {fecha_inicio_turno} '
                    f'| Km inicial: {km_inicio_turno}</p>'
                    '</div>'
                )
                st.markdown(tarjeta_en_vivo_html, unsafe_allow_html=True)

            st.caption("🟢 < 8 h  ·  🟡 8-12 h  ·  🔴 > 12 h")

        # --- Aviso aparte para turnos abiertos de semanas anteriores ---
        # No se muestran como "activos" (ya no son de esta semana), pero
        # tampoco se ocultan del todo: siguen bloqueando a ese conductor
        # para iniciar un turno nuevo, así que el admin necesita saber
        # que existen para corregirlos manualmente en el Google Sheet
        # (poniéndoles un Kilometraje Final).
        if not df_antiguos.empty:
            st.divider()
            with st.expander(f"⚠️ {len(df_antiguos)} turno(s) sin cerrar de semanas anteriores"):
                st.caption(
                    "Estos turnos probablemente quedaron abiertos por error (el conductor "
                    "olvidó dar 'Finalizar Turno'). Mientras sigan así, ese conductor no podrá "
                    "iniciar un turno nuevo. Corrígelos poniéndoles un 'Kilometraje Final' "
                    "directamente en la pestaña 'Hoja 1' de tu Google Sheet."
                )
                df_antiguos_mostrar = df_antiguos[['Fecha', 'Nombre', 'Kilometraje Inicial']].sort_values('Fecha')
                st.dataframe(df_antiguos_mostrar, use_container_width=True, hide_index=True)

# --- GENERADOR DEL REPORTE SEMANAL SOLARFLEET (.xlsx) ---
NARANJA_SF = "FFE74F25"
GRIS_CLARO_SF = "FFF2F2F2"
BLANCO_SF = "FFFFFFFF"
VERDE_SF = "FFC6EFCE"
AMARILLO_SF = "FFFFEB9C"
ROJO_SF = "FFFFC7CE"
FUENTE_SF = "Arial"

METRICAS_REPORTE = [
    ("Ganancia en efectivo", "input_dinero"),
    ("Ganancia en tarjeta", "input_dinero"),
    ("Ganancia en efectivo menos carga", "calc_efectivo_menos_carga"),
    ("Total por semana", "calc_total_semana"),
    ("Horas solicitadas", "input_horas"),
    ("horas conectadas", "input_horas"),
    ("Diferencia de horas", "calc_diferencia_horas"),
    ("Ganancia/horas conectadas", "calc_ganancia_hora"),
    ("Bono", "input_dinero"),
    ("Carga de energia", "input_dinero"),
    ("Viajes por semana", "input_entero"),
    ("Comentarios", "input_texto"),
]


def generar_reporte_semanal_xlsx(nombres_conductores, fecha_lunes, carga_por_conductor_dia):
    """Genera el reporte semanal en el formato SOLARFLEET (mismo look que el
    original, sin las referencias rotas). Prellena solo 'Carga de energia'
    con los datos que la app ya tiene; todo lo demás (efectivo, tarjeta,
    horas conectadas, viajes, bono) queda en blanco para llenarse a mano
    con los datos de DiDi. Regresa los bytes del archivo .xlsx."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Rendimiento Semanal"

    ws.column_dimensions['A'].width = 3
    ws.column_dimensions['B'].width = 3
    ws.column_dimensions['C'].width = 3
    ws.column_dimensions['D'].width = 32
    for col in "EFGHIJK":
        ws.column_dimensions[col].width = 13
    ws.column_dimensions['L'].width = 13
    ws.column_dimensions['O'].width = 10
    ws.column_dimensions['P'].width = 10
    ws.column_dimensions['Q'].width = 10

    thin = Side(style="thin", color="FFBFBFBF")
    borde = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.merge_cells('D2:F4')
    c = ws['D2']
    c.value = "SOLARFLEET"
    c.font = Font(name="Verdana", size=30, bold=True, color=BLANCO_SF)
    c.alignment = Alignment(horizontal="center", vertical="center")
    for row in ws['D2:F4']:
        for cell in row:
            cell.fill = PatternFill("solid", fgColor=NARANJA_SF)

    ws.merge_cells('O2:Q3')
    c = ws['O2']
    c.value = "RENDIMIENTO DRIVER"
    c.font = Font(name=FUENTE_SF, size=12, bold=True)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws['O5'] = "BUENO"
    ws['P5'] = "REGULAR"
    ws['Q5'] = "MALO"
    for coord, color in [('O5', VERDE_SF), ('P5', AMARILLO_SF), ('Q5', ROJO_SF)]:
        ws[coord].font = Font(name=FUENTE_SF, bold=True, size=10)
        ws[coord].fill = PatternFill("solid", fgColor=color)
        ws[coord].alignment = Alignment(horizontal="center")

    ws['O6'] = 100
    ws['P6'] = 70
    for coord in ('O6', 'P6'):
        ws[coord].font = Font(name=FUENTE_SF, size=10)
        ws[coord].alignment = Alignment(horizontal="center")
    ws['O7'] = "≥ Bueno"
    ws['P7'] = "≥ Regular"
    ws['O7'].font = ws['P7'].font = Font(name=FUENTE_SF, size=8, italic=True, color="FF808080")
    ws['O7'].alignment = ws['P7'].alignment = Alignment(horizontal="center")

    fila_header = 6
    ws.cell(row=fila_header, column=4, value="[Performance Driver]")
    ws.cell(row=fila_header, column=4).font = Font(name=FUENTE_SF, bold=True, size=10, color=BLANCO_SF)
    ws.cell(row=fila_header, column=4).fill = PatternFill("solid", fgColor=NARANJA_SF)

    dias = [fecha_lunes + timedelta(days=i) for i in range(7)]
    for i, dia in enumerate(dias):
        col = 5 + i
        cell = ws.cell(row=fila_header, column=col, value=dia)
        cell.number_format = "dd/mm/yyyy"
        cell.font = Font(name=FUENTE_SF, bold=True, size=10, color=BLANCO_SF)
        cell.fill = PatternFill("solid", fgColor=NARANJA_SF)
        cell.alignment = Alignment(horizontal="center")

    cell = ws.cell(row=fila_header, column=12, value="TOTAL")
    cell.font = Font(name=FUENTE_SF, bold=True, size=10, color=BLANCO_SF)
    cell.fill = PatternFill("solid", fgColor=NARANJA_SF)
    cell.alignment = Alignment(horizontal="center")

    filas_por_bloque = 2 + len(METRICAS_REPORTE)
    fila_actual = 8
    rangos_semaforo = []

    for driver in nombres_conductores:
        fila_bloque_header = fila_actual
        fila_nombre = fila_actual + 1
        fila_metricas_inicio = fila_actual + 2

        cell = ws.cell(row=fila_bloque_header, column=4, value="[Performance Driver]")
        cell.font = Font(name=FUENTE_SF, bold=True, size=9, color=BLANCO_SF)
        cell.fill = PatternFill("solid", fgColor=NARANJA_SF)
        for i in range(7):
            col = 5 + i
            f = ws.cell(row=fila_bloque_header, column=col, value=f"={get_column_letter(col)}${fila_header}")
            f.number_format = "dd/mm/yyyy"
            f.font = Font(name=FUENTE_SF, size=9, color=BLANCO_SF)
            f.fill = PatternFill("solid", fgColor=NARANJA_SF)
            f.alignment = Alignment(horizontal="center")
        tot = ws.cell(row=fila_bloque_header, column=12, value="TOTAL")
        tot.font = Font(name=FUENTE_SF, bold=True, size=9, color=BLANCO_SF)
        tot.fill = PatternFill("solid", fgColor=NARANJA_SF)

        ws.merge_cells(start_row=fila_nombre, start_column=4, end_row=fila_nombre, end_column=11)
        cell = ws.cell(row=fila_nombre, column=4, value=driver)
        cell.font = Font(name=FUENTE_SF, bold=True, size=11)
        for c in range(4, 13):
            ws.cell(row=fila_nombre, column=c).fill = PatternFill("solid", fgColor=GRIS_CLARO_SF)

        F = {}
        for idx, (nombre, _) in enumerate(METRICAS_REPORTE):
            F[nombre] = fila_metricas_inicio + idx

        carga_dias = carga_por_conductor_dia.get(driver, {})

        for idx, (nombre, tipo) in enumerate(METRICAS_REPORTE):
            fila = fila_metricas_inicio + idx
            etiqueta = ws.cell(row=fila, column=4, value=nombre)
            etiqueta.font = Font(name=FUENTE_SF, size=10)
            etiqueta.border = borde

            for i in range(7):
                col = 5 + i
                col_letra = get_column_letter(col)
                celda = ws.cell(row=fila, column=col)
                celda.border = borde

                if tipo == "input_dinero":
                    celda.number_format = "#,##0.00"
                    if nombre == "Carga de energia":
                        valor_real = carga_dias.get(i)
                        if valor_real:
                            celda.value = round(float(valor_real), 2)
                elif tipo == "input_horas":
                    celda.number_format = "0"
                    if nombre == "Horas solicitadas":
                        celda.value = 8
                elif tipo == "input_entero":
                    celda.number_format = "0"
                elif tipo == "input_texto":
                    pass
                elif tipo == "calc_efectivo_menos_carga":
                    f_efectivo = F["Ganancia en efectivo"]
                    f_carga = F["Carga de energia"]
                    celda.value = f"=IFERROR({col_letra}{f_efectivo}-{col_letra}{f_carga},{col_letra}{f_efectivo})"
                    celda.number_format = "#,##0.00"
                elif tipo == "calc_total_semana":
                    f_ef = F["Ganancia en efectivo menos carga"]
                    f_tj = F["Ganancia en tarjeta"]
                    celda.value = f"=SUM({col_letra}{f_ef},{col_letra}{f_tj})"
                    celda.number_format = "#,##0.00"
                elif tipo == "calc_diferencia_horas":
                    f_con = F["horas conectadas"]
                    f_sol = F["Horas solicitadas"]
                    celda.value = f"={col_letra}{f_con}-{col_letra}{f_sol}"
                    celda.number_format = "0"
                elif tipo == "calc_ganancia_hora":
                    f_tot = F["Total por semana"]
                    f_con = F["horas conectadas"]
                    celda.value = f"=IFERROR({col_letra}{f_tot}/{col_letra}{f_con},0)"
                    celda.number_format = "#,##0.00"

            celda_l = ws.cell(row=fila, column=12)
            celda_l.border = borde
            if tipo == "input_texto":
                pass
            elif tipo == "calc_diferencia_horas":
                f_con = F["horas conectadas"]
                f_sol = F["Horas solicitadas"]
                celda_l.value = f"=L{f_con}-L{f_sol}"
                celda_l.number_format = "0"
            elif tipo == "calc_ganancia_hora":
                f_tot = F["Total por semana"]
                f_con = F["horas conectadas"]
                celda_l.value = f"=IFERROR(L{f_tot}/L{f_con},0)"
                celda_l.number_format = "#,##0.00"
                rangos_semaforo.append(f"L{fila}")
            else:
                celda_l.value = f"=SUM(E{fila}:K{fila})"
                celda_l.number_format = "#,##0.00" if tipo != "input_entero" else "0"

        fila_actual += filas_por_bloque

    if rangos_semaforo:
        rango_semaforo = " ".join(rangos_semaforo)
        ws.conditional_formatting.add(
            rango_semaforo,
            CellIsRule(operator="greaterThanOrEqual", formula=["$O$6"], fill=PatternFill("solid", fgColor=VERDE_SF)),
        )
        ws.conditional_formatting.add(
            rango_semaforo,
            CellIsRule(operator="between", formula=["$P$6", "$O$6"], fill=PatternFill("solid", fgColor=AMARILLO_SF)),
        )
        ws.conditional_formatting.add(
            rango_semaforo,
            CellIsRule(operator="lessThan", formula=["$P$6"], fill=PatternFill("solid", fgColor=ROJO_SF)),
        )

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def obtener_carga_por_conductor_dia(conn, fecha_lunes):
    """Suma 'Carga del Día' de Hoja 1 por conductor y día de la semana
    (0=lunes..6=domingo), para prellenar el reporte semanal."""
    df = conn.read(worksheet="Hoja 1", ttl=30)
    if df.empty:
        return {}
    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df = df.dropna(subset=['Fecha'])
    fecha_domingo = fecha_lunes + timedelta(days=6)
    df = df[(df['Fecha'].dt.date >= fecha_lunes) & (df['Fecha'].dt.date <= fecha_domingo)]
    if df.empty:
        return {}
    df['Carga del Día'] = pd.to_numeric(df['Carga del Día'], errors='coerce').fillna(0)
    df['dia_idx'] = (df['Fecha'].dt.date - fecha_lunes).apply(lambda d: d.days)

    resultado = {}
    for nombre, grupo in df.groupby(df['Nombre'].astype(str).str.strip()):
        por_dia = grupo.groupby('dia_idx')['Carga del Día'].sum().to_dict()
        resultado[nombre] = por_dia
    return resultado


# --- PESTAÑA 4: DASHBOARD ADMIN ---
if es_admin:
    with tab_dash:
        st.header("Análisis de Operación y Carga")
        df_dash = conn.read(worksheet="Hoja 1", ttl=0)
        df_dash = asegurar_columnas(df_dash, COLUMNAS_ESPERADAS)

        if not df_dash.empty:
            df_dash['Fecha'] = pd.to_datetime(df_dash['Fecha'], errors='coerce')
            df_dash = df_dash.dropna(subset=['Fecha'])

            if not df_dash.empty:
                df_dash['Semana'] = df_dash['Fecha'].dt.strftime('%Y - Sem %U')
                df_dash['Total Recorrido'] = pd.to_numeric(df_dash['Total Recorrido'], errors='coerce').fillna(0)
                df_dash['Carga del Día'] = pd.to_numeric(df_dash['Carga del Día'], errors='coerce').fillna(0)

                lista_semanas = sorted(df_dash['Semana'].unique(), reverse=True)
                col_f1, col_f2 = st.columns(2)
                with col_f1:
                    semana_sel = st.selectbox("📅 Selecciona la Semana:", ["Todas"] + lista_semanas)
                with col_f2:
                    lista_conductores_dash = sorted(df_dash['Nombre'].dropna().astype(str).str.strip().unique())
                    conductor_sel = st.selectbox("🧑‍✈️ Filtrar por conductor:", ["Todos"] + lista_conductores_dash)

                df_f = df_dash if semana_sel == "Todas" else df_dash[df_dash['Semana'] == semana_sel]
                if conductor_sel != "Todos":
                    df_f = df_f[df_f['Nombre'].astype(str).str.strip() == conductor_sel]

                df_f['Lugar de Carga'] = df_f['Lugar de Carga'].astype(str).str.strip().str.title()
                df_lugares = df_f[~df_f['Lugar de Carga'].isin(["N/A", "None", "", "Nan"])]
                conteo_lugares = df_lugares['Lugar de Carga'].value_counts().reset_index()
                conteo_lugares.columns = ['Lugar', 'Número de Cargas']
                resumen_gastos = df_f.groupby('Nombre')['Carga del Día'].sum().reset_index()

                m1, m2, m3 = st.columns(3)
                m1.metric("KM Totales", f"{df_f['Total Recorrido'].sum():,.1f} km")
                m2.metric("Gasto Total", f"${df_f['Carga del Día'].sum():,.2f}")
                m3.metric("Estaciones Visitadas", len(conteo_lugares))

                abiertos = df_dash[pd.isna(df_dash['Kilometraje Final']) | (df_dash['Kilometraje Final'] == "")].copy()
                if not abiertos.empty:
                    abiertos['Horas Abierto'] = abiertos['Fecha'].apply(horas_desde)
                    largos = abiertos[abiertos['Horas Abierto'] > 12]
                    if not largos.empty:
                        st.warning(
                            f"⚠️ {len(largos)} turno(s) abiertos hace más de 12 horas: "
                            + ", ".join(largos['Nombre'].astype(str).unique())
                        )

                st.divider()
                col_g1, col_g2 = st.columns(2)
                with col_g1:
                    st.subheader("📍 Lugares Frecuentados")
                    if not conteo_lugares.empty:
                        st.bar_chart(data=conteo_lugares, x='Lugar', y='Número de Cargas')
                with col_g2:
                    st.subheader("💰 Gasto por Conductor")
                    st.bar_chart(data=resumen_gastos, x='Nombre', y='Carga del Día')

                st.divider()
                csv_bytes = df_f.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "⬇️ Descargar reporte de esta semana (CSV)", data=csv_bytes,
                    file_name=f"reporte_flotilla_{semana_sel.replace(' ', '_')}.csv",
                    mime="text/csv", use_container_width=True,
                )

        # --- REPORTE SEMANAL SOLARFLEET (.xlsx) ---
        st.divider()
        st.subheader("📊 Reporte Semanal SOLARFLEET (Excel)")
        st.caption(
            "Genera el formato semanal de Rendimiento Driver, con los conductores activos y "
            "las fechas ya puestas. La fila 'Carga de energía' se llena sola con los datos de "
            "la app; el resto (efectivo, tarjeta, horas, viajes, bono) lo completas a mano con "
            "los datos de DiDi."
        )
        fecha_lunes_reporte = st.date_input(
            "Lunes de la semana a generar:",
            value=datetime.now(zona_cdmx).date(),
            key="fecha_lunes_reporte",
        )
        # Nos aseguramos de partir siempre de un lunes, sin importar qué día elija el admin.
        fecha_lunes_reporte = fecha_lunes_reporte - timedelta(days=fecha_lunes_reporte.weekday())
        st.caption(f"Semana del {fecha_lunes_reporte.strftime('%d/%m/%Y')} al {(fecha_lunes_reporte + timedelta(days=6)).strftime('%d/%m/%Y')}")

        if st.button("📄 Generar reporte semanal", use_container_width=True):
            df_conductores_activos = leer_usuarios_fresco(conn)
            df_conductores_activos = df_conductores_activos.dropna(subset=['Username'])
            nombres_activos = [
                str(fila['Nombre']).strip()
                for _, fila in df_conductores_activos.iterrows()
                if esta_activo(fila.get('Activo')) and str(fila['Nombre']).strip()
            ]

            if not nombres_activos:
                st.warning("⚠️ No hay conductores activos registrados.")
            else:
                with st.spinner("Generando reporte..."):
                    carga_dia = obtener_carga_por_conductor_dia(conn, fecha_lunes_reporte)
                    xlsx_bytes = generar_reporte_semanal_xlsx(nombres_activos, fecha_lunes_reporte, carga_dia)

                st.success(f"✅ Reporte generado con {len(nombres_activos)} conductores.")
                st.download_button(
                    "⬇️ Descargar reporte semanal SOLARFLEET (.xlsx)",
                    data=xlsx_bytes,
                    file_name=f"reporte_solarfleet_{fecha_lunes_reporte.strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

        # --- GESTIÓN DE CONDUCTORES (alta de usuarios) ---
        st.divider()
        st.subheader("👥 Gestión de Conductores")
        st.caption("Da de alta a un nuevo conductor con su propio usuario y contraseña.")

        with st.form("form_nuevo_conductor", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                nombre_nuevo = st.text_input("Nombre completo")
                username_nuevo = st.text_input("Username (sin espacios)")
            with c2:
                password_nuevo = st.text_input("Contraseña temporal", type="password")
                rol_nuevo = st.selectbox("Rol", ["conductor", "admin"])

            enviado = st.form_submit_button("➕ Crear conductor", type="primary", use_container_width=True)
            if enviado:
                if not (nombre_nuevo and username_nuevo and password_nuevo):
                    st.warning("⚠️ Completa nombre, username y contraseña.")
                else:
                    ok, mensaje = agregar_usuario(
                        conn, nombre_nuevo.strip(), username_nuevo.strip(), password_nuevo, rol_nuevo
                    )
                    (st.success if ok else st.error)(mensaje)

        # --- DAR DE BAJA / REACTIVAR CONDUCTORES ---
        st.divider()
        col_titulo, col_refrescar = st.columns([4, 1])
        with col_titulo:
            st.subheader("🚫 Dar de Baja / Reactivar Conductor")
        with col_refrescar:
            if st.button("🔄 Actualizar lista"):
                invalidar_cache_usuarios()
                st.rerun()
        st.caption(
            "Desactivar un conductor le quita el acceso a la app, "
            "pero conserva todo su historial de turnos. Puedes reactivarlo cuando quieras."
        )

        df_usuarios_actual = leer_usuarios_fresco(conn)
        df_usuarios_actual = df_usuarios_actual.dropna(subset=['Username'])

        if df_usuarios_actual.empty:
            st.info("Aún no hay conductores registrados.")
        else:
            # IMPORTANTE: el selector solo muestra nombre + username, algo
            # que NUNCA cambia. El estado (Activo/Dado de baja) se calcula
            # y se muestra APARTE con st.markdown, no dentro de la opción
            # del selector — así siempre se recalcula fresco en cada
            # ejecución y refleja exactamente lo que dice la hoja de
            # Google Sheets, sin depender de que el navegador redibuje
            # el texto interno del selector (que a veces se queda viejo).
            nombres_por_usuario = {}
            for _, fila in df_usuarios_actual.iterrows():
                uname = str(fila['Username']).strip()
                nombres_por_usuario[uname] = str(fila['Nombre']).strip()

            lista_usernames = sorted(nombres_por_usuario.keys())
            uname_sel = st.selectbox(
                "Selecciona un conductor:",
                lista_usernames,
                format_func=lambda u: f"{nombres_por_usuario.get(u, u)} ({u})",
            )

            # Estado leído fresco, directo de la fila actual del DataFrame
            # recién descargado — este valor manda, siempre acorde a la hoja.
            fila_sel = df_usuarios_actual[
                df_usuarios_actual['Username'].astype(str).str.strip() == uname_sel
            ].iloc[0]
            activo_sel = esta_activo(fila_sel.get('Activo'))

            if activo_sel:
                st.markdown(f"**Estado actual:** {badge_estado(True)}", unsafe_allow_html=True)
            else:
                st.markdown(f"**Estado actual:** {badge_estado(False)}", unsafe_allow_html=True)

            col_b1, col_b2 = st.columns(2)
            with col_b1:
                if st.button("🔴 Dar de baja", disabled=not activo_sel, use_container_width=True):
                    ok, mensaje = cambiar_estado_usuario(conn, uname_sel, activar=False)
                    (st.success if ok else st.error)(mensaje)
                    if ok:
                        st.rerun()
            with col_b2:
                if st.button("🟢 Reactivar", disabled=activo_sel, use_container_width=True):
                    ok, mensaje = cambiar_estado_usuario(conn, uname_sel, activar=True)
                    (st.success if ok else st.error)(mensaje)
                    if ok:
                        st.rerun()

            # --- RESETEAR CONTRASEÑA ---
            st.divider()
            st.subheader("🔑 Resetear Contraseña")
            st.caption("Úsalo cuando un conductor olvide su contraseña. Comparte la nueva con él por fuera de la app.")

            with st.form("form_reset_password", clear_on_submit=True):
                nueva_pw = st.text_input("Nueva contraseña temporal", type="password")
                confirmar_reset = st.form_submit_button(
                    f"🔑 Restablecer contraseña de {uname_sel}", type="primary", use_container_width=True
                )
                if confirmar_reset:
                    if not nueva_pw:
                        st.warning("⚠️ Escribe la nueva contraseña.")
                    elif len(nueva_pw) < 6:
                        st.warning("⚠️ Usa al menos 6 caracteres.")
                    else:
                        ok, mensaje = resetear_password(conn, uname_sel, nueva_pw)
                        (st.success if ok else st.error)(mensaje)

        # --- GESTOR DE ARCHIVOS (LIBERAR ESPACIO) ---
        # Se movió fuera de la vista normal del admin: es una acción
        # irreversible (borra archivos permanentemente) y no es algo que
        # se use seguido, así que ahora vive detrás de un modo de
        # mantenimiento para evitar clics accidentales. Para verla, entra
        # con ?mantenimiento=true además de ?jefe=true en la URL.
        modo_mantenimiento = st.query_params.get("mantenimiento") == "true"
        if modo_mantenimiento:
            st.divider()
            st.subheader("🧹 Gestor de Archivos (Liberar Espacio)")
            st.warning("⚠️ Modo mantenimiento — esta acción borra archivos de forma permanente e irreversible.")
            link_a_borrar = st.text_input("Enlace del Comprobante (URL):", placeholder="https://res.cloudinary.com/...")

            if st.button("🗑️ Eliminar permanentemente de la nube", type="primary"):
                if "cloudinary.com" in link_a_borrar:
                    with st.spinner("Borrando archivo..."):
                        try:
                            public_id, res_type = extraer_datos_cloudinary(link_a_borrar)
                            respuesta = cloudinary.uploader.destroy(public_id, resource_type=res_type)
                            if respuesta.get('result') == 'ok':
                                st.success("✅ Archivo eliminado correctamente. ¡Espacio liberado!")
                            else:
                                st.warning("⚠️ No se encontró el archivo. Es probable que ya haya sido borrado.")
                        except Exception as e:
                            st.error(f"Error técnico al intentar borrar: {e}")
                else:
                    st.error("❌ Por favor ingresa un enlace válido de Cloudinary.")

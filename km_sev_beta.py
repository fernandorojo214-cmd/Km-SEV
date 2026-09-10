import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import re
import cloudinary
import cloudinary.uploader
import cloudinary.api
import streamlit_authenticator as stauth
import bcrypt
from streamlit_gsheets import GSheetsConnection

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
        if f.tzinfo is None:
            f = zona_cdmx.localize(f)
        return (datetime.now(zona_cdmx) - f).total_seconds() / 3600
    except Exception:
        return None


def esta_activo(valor):
    """Interpreta la columna 'Activo' de forma flexible: TRUE/Sí/1 = activo.
    Si la celda está vacía (filas viejas antes de agregar esta columna),
    se considera activo por defecto para no bloquear a nadie sin querer."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return True
    texto = str(valor).strip().lower()
    if texto in ("", "nan", "none"):
        return True
    return texto in ("true", "verdadero", "si", "sí", "1", "activo")


@st.cache_data(ttl=60)
def leer_usuarios(_conn):
    """Lectura centralizada y cacheada de la pestaña 'Usuarios'.
    Antes cada sección del Dashboard leía la hoja por su cuenta con
    ttl=0 (sin caché), lo que disparaba demasiadas solicitudes a la
    API de Google Sheets y provocaba errores de límite de solicitudes
    (APIError / rate limit). Ahora todo pasa por aquí."""
    df = _conn.read(worksheet="Usuarios", ttl=60)
    df.columns = [str(c).strip() for c in df.columns]
    df = asegurar_columnas(df, COLUMNAS_USUARIOS)
    return df


@st.cache_data(ttl=60)
def cargar_credenciales(_conn):
    """Lee la hoja 'Usuarios' y arma el diccionario que necesita
    streamlit-authenticator: {usernames: {user: {name, password, role}}}"""
    df = leer_usuarios(_conn)
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
        df = leer_usuarios(conn)

        if username in df['Username'].astype(str).str.strip().values:
            return False, "Ese username ya existe. Elige otro."

        hash_pw = bcrypt.hashpw(password_plano.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        nueva_fila = {"Nombre": nombre, "Username": username, "Password": hash_pw, "Rol": rol, "Activo": "TRUE"}
        df = pd.concat([df, pd.DataFrame([nueva_fila])], ignore_index=True)

        # Solo escribimos las 4 columnas que nos interesan, en orden fijo,
        # para no arrastrar columnas viejas/duplicadas a la hoja.
        df = df[COLUMNAS_USUARIOS]

        conn.update(worksheet="Usuarios", data=df)
        st.cache_data.clear()
        return True, "Conductor agregado correctamente."
    except Exception as e:
        return False, _mensaje_error_amigable(e)


def cambiar_estado_usuario(conn, username, activar: bool):
    """Da de baja (o reactiva) a un conductor sin borrar su historial.
    Simplemente le apaga el acceso cambiando la columna 'Activo'."""
    try:
        df = leer_usuarios(conn)

        username_buscado = username.strip().lower()
        mascara = df['Username'].astype(str).str.strip().str.lower() == username_buscado

        if not mascara.any():
            return False, "No se encontró ese conductor."

        df.loc[mascara, 'Activo'] = "TRUE" if activar else "FALSE"
        df = df[COLUMNAS_USUARIOS]
        conn.update(worksheet="Usuarios", data=df)
        st.cache_data.clear()

        accion = "reactivado" if activar else "dado de baja"
        return True, f"Conductor {accion} correctamente."
    except Exception as e:
        return False, _mensaje_error_amigable(e)


def resetear_password(conn, username, nueva_password_plano):
    """Permite al admin poner una contraseña temporal nueva a un conductor
    que la olvidó, sin necesidad de tocar el Google Sheet a mano."""
    try:
        df = leer_usuarios(conn)

        username_buscado = username.strip().lower()
        mascara = df['Username'].astype(str).str.strip().str.lower() == username_buscado

        if not mascara.any():
            return False, "No se encontró ese conductor."

        hash_pw = bcrypt.hashpw(nueva_password_plano.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        df.loc[mascara, 'Password'] = hash_pw
        df = df[COLUMNAS_USUARIOS]
        conn.update(worksheet="Usuarios", data=df)
        st.cache_data.clear()
        return True, "Contraseña restablecida correctamente. Comparte la nueva contraseña con el conductor."
    except Exception as e:
        return False, _mensaje_error_amigable(e)


# --- ENCABEZADO ---
col1, col2 = st.columns([1, 4])
with col1:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=200)
with col2:
    st.markdown("<h1 style='margin-top: 25px;'>Control de Flotilla SEV</h1>", unsafe_allow_html=True)
st.divider()

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
nombres_tabs = ["🟢 Iniciar Turno", "🔴 Finalizar Turno", "📋 Mi Historial"]
if es_admin:
    nombres_tabs.append("📊 Dashboard Admin")

tabs = st.tabs(nombres_tabs)
tab_inicio, tab_fin, tab_historial = tabs[0], tabs[1], tabs[2]
if es_admin:
    tab_dash = tabs[3]

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
        ["📷 Tomar foto ahora", "📁 Subir archivo (foto o PDF)"],
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

        turno_pendiente = propios[pd.isna(propios['Kilometraje Final']) | (propios['Kilometraje Final'] == "")]
        if not turno_pendiente.empty:
            fila = turno_pendiente.iloc[0]
            horas = horas_desde(fila['Fecha'])
            aviso = f"🟡 Tienes un turno abierto desde {fila['Fecha']}"
            if horas is not None:
                aviso += f" (hace {horas:.1f} horas)"
            (st.warning if (horas or 0) > 12 else st.info)(aviso)

        total_km = pd.to_numeric(propios['Total Recorrido'], errors='coerce').fillna(0).sum()
        total_gasto = pd.to_numeric(propios['Carga del Día'], errors='coerce').fillna(0).sum()

        m1, m2, m3 = st.columns(3)
        m1.metric("Turnos registrados", len(propios))
        m2.metric("KM totales", f"{total_km:,.1f} km")
        m3.metric("Gasto total en carga", f"${total_gasto:,.2f}")

        st.divider()
        st.dataframe(
            propios[['Fecha', 'Kilometraje Inicial', 'Kilometraje Final', 'Total Recorrido',
                     'Carga del Día', 'Lugar de Carga', 'Comentarios']],
            use_container_width=True, hide_index=True
        )

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
        st.subheader("🚫 Dar de Baja / Reactivar Conductor")
        st.caption(
            "Desactivar un conductor le quita el acceso a la app, "
            "pero conserva todo su historial de turnos. Puedes reactivarlo cuando quieras."
        )

        df_usuarios_actual = leer_usuarios(conn)
        df_usuarios_actual = df_usuarios_actual.dropna(subset=['Username'])

        if df_usuarios_actual.empty:
            st.info("Aún no hay conductores registrados.")
        else:
            opciones_usuarios = {}
            for _, fila in df_usuarios_actual.iterrows():
                uname = str(fila['Username']).strip()
                nombre_disp = str(fila['Nombre']).strip()
                activo = esta_activo(fila.get('Activo'))
                etiqueta = f"{nombre_disp} ({uname}) — {'🟢 Activo' if activo else '🔴 Dado de baja'}"
                opciones_usuarios[etiqueta] = (uname, activo)

            seleccion_usuario = st.selectbox("Selecciona un conductor:", list(opciones_usuarios.keys()))
            uname_sel, activo_sel = opciones_usuarios[seleccion_usuario]

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

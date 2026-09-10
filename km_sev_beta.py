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
COLUMNAS_USUARIOS = ['Nombre', 'Username', 'Password', 'Rol']

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


@st.cache_data(ttl=30)
def cargar_credenciales(_conn):
    """Lee la hoja 'Usuarios' y arma el diccionario que necesita
    streamlit-authenticator: {usernames: {user: {name, password, role}}}"""
    df = _conn.read(worksheet="Usuarios", ttl=30)
    df = asegurar_columnas(df, COLUMNAS_USUARIOS)
    df = df.dropna(subset=['Username'])

    credenciales = {"usernames": {}}
    for _, fila in df.iterrows():
        username = str(fila['Username']).strip()
        if not username:
            continue
        credenciales["usernames"][username] = {
            "name": str(fila['Nombre']).strip(),
            "password": str(fila['Password']).strip(),  # ya viene hasheado
            "role": str(fila.get('Rol', 'conductor')).strip().lower() or "conductor",
            # streamlit-authenticator 0.4.x espera este campo aunque no
            # usemos recuperación de contraseña por correo; lo dejamos vacío.
            "email": "",
        }
    return credenciales


def agregar_usuario(conn, nombre, username, password_plano, rol):
    """Usado por el admin para dar de alta un nuevo conductor.
    Hashea la contraseña antes de guardarla — nunca se guarda en texto plano."""
    df = conn.read(worksheet="Usuarios", ttl=0)
    df = asegurar_columnas(df, COLUMNAS_USUARIOS)

    if username in df['Username'].astype(str).values:
        return False, "Ese username ya existe. Elige otro."

    hash_pw = bcrypt.hashpw(password_plano.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    nueva_fila = {"Nombre": nombre, "Username": username, "Password": hash_pw, "Rol": rol}
    df = pd.concat([df, pd.DataFrame([nueva_fila])], ignore_index=True)
    conn.update(worksheet="Usuarios", data=df)
    st.cache_data.clear()
    return True, "Conductor agregado correctamente."


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
credenciales = cargar_credenciales(conn)

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
                semana_sel = st.selectbox("📅 Selecciona la Semana:", ["Todas"] + lista_semanas)
                df_f = df_dash if semana_sel == "Todas" else df_dash[df_dash['Semana'] == semana_sel]

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

        # --- GESTOR DE ARCHIVOS (LIBERAR ESPACIO) ---
        st.divider()
        st.subheader("🧹 Gestor de Archivos (Liberar Espacio)")
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

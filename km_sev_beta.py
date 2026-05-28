import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import re
import cloudinary
import cloudinary.uploader
import cloudinary.api
from streamlit_gsheets import GSheetsConnection

# --- CONFIGURACIÓN DE CLOUDINARY ---
cloudinary.config(
    cloud_name = "TU_CLOUD_NAME",
    api_key = "TU_API_KEY",
    api_secret = "TU_API_SECRET",
    secure = True
)

st.set_page_config(page_title="SEVTrack Beta", layout="centered")

# --- VARIABLES DE SESIÓN (EL CEREBRO DEL LOGIN) ---
# Aquí guardamos temporalmente si el usuario ya entró y en qué pantalla de login está
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'current_user' not in st.session_state:
    st.session_state['current_user'] = ""
if 'pantalla_auth' not in st.session_state:
    st.session_state['pantalla_auth'] = 'login'

# Simulador de Base de Datos de Usuarios para la fase Beta
if 'db_usuarios' not in st.session_state:
    st.session_state['db_usuarios'] = {
        'admin': 'admin123',
        'juan': '1234',
        'pedro': 'abcd'
    }

# --- FUNCIONES DE NAVEGACIÓN ---
def ir_a_login(): st.session_state['pantalla_auth'] = 'login'
def ir_a_registro(): st.session_state['pantalla_auth'] = 'registro'
def ir_a_recuperar(): st.session_state['pantalla_auth'] = 'recuperar'
def cerrar_sesion():
    st.session_state['logged_in'] = False
    st.session_state['current_user'] = ""
    st.session_state['pantalla_auth'] = 'login'

# --- FUNCIONES DE CLOUDINARY ---
def subir_archivo_a_nube(file_obj):
    try:
        es_pdf = file_obj.name.lower().endswith('.pdf')
        tipo_recurso = "raw" if es_pdf else "auto"
        resultado = cloudinary.uploader.upload(
            file_obj, resource_type=tipo_recurso, use_filename=True, unique_filename=True)
        return resultado['secure_url']
    except Exception as e:
        st.error(f"Error al subir: {e}")
        return None

def calcular_total_carga(texto):
    numeros = re.findall(r"[-+]?\d*\.\d+|\d+", texto)
    if not numeros: return 0.0
    return sum(float(n) for n in numeros)

# --- PANTALLAS DE AUTENTICACIÓN (LOGIN) ---
if not st.session_state['logged_in']:
    
    # Encabezado para la zona de Login
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<h2 style='text-align: center;'>SOLAR FLEET</h2>", unsafe_allow_html=True)
        st.divider()

        # 1. PANTALLA DE LOGIN NORMAL
        if st.session_state['pantalla_auth'] == 'login':
            usuario = st.text_input("Usuario (Ej. juan)").strip().lower()
            password = st.text_input("Contraseña", type="password")
            
            if st.button("Iniciar Sesión", type="primary", use_container_width=True):
                if usuario in st.session_state['db_usuarios'] and st.session_state['db_usuarios'][usuario] == password:
                    st.session_state['logged_in'] = True
                    st.session_state['current_user'] = usuario.title() # Lo guardamos con Mayúscula
                    st.rerun() # Recarga la página para mostrar la app
                else:
                    st.error("❌ Usuario o contraseña incorrectos.")
            
            st.write("")
            c1, c2 = st.columns(2)
            c1.button("Crear cuenta", on_click=ir_a_registro, use_container_width=True)
            c2.button("¿Olvidaste tu clave?", on_click=ir_a_recuperar, use_container_width=True)

        # 2. PANTALLA DE REGISTRO
        elif st.session_state['pantalla_auth'] == 'registro':
            st.subheader("Crear nueva cuenta")
            nuevo_usr = st.text_input("Nombre").strip().lower()
            nuevo_ape= st.tex_input("Apellido")
            nueva_pass = st.text_input("Crea una contraseña", type="password")
            pass_conf = st.text_input("Confirma tu contraseña", type="password")
            
            if st.button("Registrarse", type="primary", use_container_width=True):
                if nuevo_usr in st.session_state['db_usuarios']:
                    st.error("Ese usuario ya existe.")
                elif nueva_pass != pass_conf:
                    st.error("Las contraseñas no coinciden.")
                elif len(nueva_pass) < 4:
                    st.error("La contraseña debe tener al menos 4 caracteres.")
                else:
                    st.session_state['db_usuarios'][nuevo_usr] = nueva_pass
                    st.success("¡Cuenta creada! Ya puedes iniciar sesión.")
                    ir_a_login()
            
            st.button("🔙 Volver al Login", on_click=ir_a_login, use_container_width=True)

        # 3. PANTALA RECUPERACION DE CONTRASEÑA
        elif st.session_state['pantalla_auth'] == 'recuperar':
            st.subheader("Recuperar contraseña")
            st.write("Para esta fase Beta, por favor contacta al administrador para reiniciar tu clave.")
            usr_olvido = st.text_input("Ingresa tu usuario:")
            if st.button("Solicitar reinicio", type="primary", use_container_width=True):
                if usr_olvido:
                    st.success(f"Se ha notificado a administración para revisar la cuenta de: {usr_olvido}")
                else:
                    st.warning("Escribe tu usuario primero.")
            
            st.button("🔙 Volver al Login", on_click=ir_a_login, use_container_width=True)

# --- APLICACIÓN PRINCIPAL (SOLO VISIBLE SI INICIÓ SESIÓN) ---
else:
    # Encabezado SEVTrack
    c1, c2, c3 = st.columns([1, 3, 1])
    with c1:
        if os.path.exists("logo.png"): st.image("logo.png", width=150)
    with c2:
        st.markdown("<h2 style='margin-top: 15px;'>Control SEV</h2>", unsafe_allow_html=True)
    with c3:
        st.write(f"👤 **{st.session_state['current_user']}**")
        st.button("Cerrar Sesión", on_click=cerrar_sesion)

    st.divider()

    conn = st.connection("gsheets", type=GSheetsConnection)
    zona_cdmx = pytz.timezone('America/Mexico_City')
    
    # Validar si es admin usando la puerta trasera (URL) o si inició sesión con el usuario 'admin'
    es_admin = False
    if ("jefe" in st.query_params and st.query_params["jefe"] == "true") or (st.session_state['current_user'].lower() == 'admin'):
        es_admin = True

    nombres_tabs = ["🟢 Iniciar Turno", "🔴 Finalizar Turno"]
    if es_admin: nombres_tabs.append("📊 Dashboard Admin")
    
    tabs = st.tabs(nombres_tabs)

    # --- PESTAÑA 1: INICIO DE TURNO ---
    with tabs[0]:
        st.header("Registro de Inicio")
        
        # EL GRAN CAMBIO: El nombre ya no se puede editar, viene del Login
        nombre_inicio = st.text_input("Conductor", value=st.session_state['current_user'], disabled=True)
        km_inicio = st.number_input("Kilometraje Inicial", min_value=0.0, step=0.1, value=None)
        
        if st.button("Registrar Inicio", type="primary"):
            if km_inicio is not None:
                df = conn.read(worksheet="Hoja Beta", ttl=0) # Usamos Hoja Beta
                
                for col in ['Carga del Día', 'Lugar de Carga', 'Comentarios', 'Comprobante']:
                    if col not in df.columns: df[col] = ""
                    df[col] = df[col].astype("object")
                
                nombre_buscado = nombre_inicio.strip().lower()
                abierto = df[(df['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado) & 
                             (pd.isna(df['Kilometraje Final']) | (df['Kilometraje Final'] == ""))]
                
                if not abierto.empty:
                    st.error("⚠️ Ya tienes un turno activo.")
                else:
                    ahora = datetime.now(zona_cdmx).strftime("%Y-%m-%d %H:%M:%S")
                    nuevo = {'Fecha': ahora, 'Nombre': nombre_inicio, 'Kilometraje Inicial': float(km_inicio)}
                    df = pd.concat([df, pd.DataFrame([nuevo])], ignore_index=True)
                    conn.update(worksheet="Hoja Beta", data=df)
                    st.cache_data.clear()
                    st.success("✅ ¡Buen viaje!")
            else:
                st.warning("⚠️ Ingresa el kilometraje.")

    # --- PESTAÑA 2: FIN DE TURNO ---
    with tabs[1]:
        st.header("Registro Final")
        # Nombre bloqueado automáticamente
        nombre_fin = st.text_input("Nombre", value=st.session_state['current_user'], disabled=True, key="nom_f")
        km_fin = st.number_input("Kilometraje Final", min_value=0.0, step=0.1, value=None)
        carga = st.text_input("Carga ($)", placeholder="Ej: 500")
        lugar = st.text_input("Lugar de Carga")
        coments = st.text_area("Comentarios")
        archivos = st.file_uploader("Subir fotos o PDFs", type=["png", "jpg", "jpeg", "pdf"], accept_multiple_files=True)
        
        if st.button("Registrar Fin", type="primary"):
            if km_fin is not None:
                df = conn.read(worksheet="Hoja Beta", ttl=0)
                
                for col in ['Carga del Día', 'Lugar de Carga', 'Comentarios', 'Comprobante']:
                    if col not in df.columns: df[col] = ""
                    df[col] = df[col].astype("object")

                nombre_buscado = nombre_fin.strip().lower()
                pend = df[(df['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado) & 
                          (pd.isna(df['Kilometraje Final']) | (df['Kilometraje Final'] == ""))]
                
                if not pend.empty:
                    idx = pend.index[-1]
                    km_ini = float(df.at[idx, 'Kilometraje Inicial'])
                    
                    if km_fin >= km_ini:
                        total_dinero = calcular_total_carga(carga)
                        links = []
                        if archivos:
                            with st.spinner("Subiendo archivos..."):
                                for arc in archivos:
                                    url = subir_archivo_a_nube(arc)
                                    if url: links.append(url)
                        
                        df.at[idx, 'Kilometraje Final'] = float(km_fin)
                        df.at[idx, 'Total Recorrido'] = km_fin - km_ini
                        df.at[idx, 'Carga del Día'] = total_dinero
                        df.at[idx, 'Lugar de Carga'] = lugar
                        df.at[idx, 'Comentarios'] = coments
                        df.at[idx, 'Comprobante'] = ", ".join(links)
                        
                        conn.update(worksheet="Hoja Beta", data=df)
                        st.cache_data.clear()
                        st.success(f"🏁 ¡Turno cerrado! Recorriste {km_fin - km_ini} km.")
                        st.balloons()
                    else:
                        st.error("❌ El KM final es menor al inicial.")
                else:
                    st.error("❌ No hay turno activo a tu nombre.")
            else:
                st.warning("⚠️ Faltan datos.")

    # --- PESTAÑA 3: ADMIN ---
    if es_admin:
        with tabs[2]:
            st.info("Área Administrativa Activa. (Aquí va el código de gráficas y limpieza de tu app principal)")

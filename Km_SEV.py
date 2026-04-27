import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import cloudinary
import cloudinary.uploader
import re
from streamlit_gsheets import GSheetsConnection

# --- CONFIGURACIÓN DE CLOUDINARY ---
# Reemplaza con tus datos de Cloudinary
cloudinary.config(
  cloud_name = "dlycnr93r",
  api_key = "594752421538947",
  api_secret = "SrANNdM7fAUnpV6ZW3eHbCqL1YQ",
  secure = True
)

st.set_page_config(page_title="Control de Flotilla", layout="centered")

# --- FUNCIONES DE APOYO ---
def subir_archivo_a_nube(file_obj):
    try:
        # 'resource_type="auto"' permite que Cloudinary acepte tanto fotos como PDFs
        resultado = cloudinary.uploader.upload(file_obj, resource_type="auto")
        return resultado['secure_url']
    except Exception as e:
        st.error(f"Error al subir archivo: {e}")
        return None

def calcular_total_carga(texto):
    numeros = re.findall(r"[-+]?\d*\.\d+|\d+", texto)
    if not numeros: return 0.0
    return sum(float(n) for n in numeros)

# --- ENCABEZADO PERSONALIZADO ---
col1, col2 = st.columns([1, 4])
with col1:
    try:
        if os.path.exists("logo.png"): 
            st.image("logo.png", width=200)
    except: 
        pass
with col2:
    st.markdown("<h1 style='margin-top: 25px;'>Control de Flotilla SEV</h1>", unsafe_allow_html=True)

st.divider()

# --- SEGURIDAD: PUERTA TRASERA INVISIBLE (URL SECRETA) ---
es_admin = False
if "jefe" in st.query_params and st.query_params["jefe"] == "true":
    with st.sidebar:
        st.title("🔐 Zona Segura")
        password_admin = st.text_input("Ingresa tu PIN", type="password")
        if password_admin == "admin123":
            es_admin = True
            st.success("Acceso concedido")

st.write("")

conn = st.connection("gsheets", type=GSheetsConnection)
zona_cdmx = pytz.timezone('America/Mexico_City')

nombres_tabs = ["🟢 Iniciar Turno", "🔴 Finalizar Turno"]
if es_admin:
    nombres_tabs.append("📊 Dashboard Admin")

tabs = st.tabs(nombres_tabs)
tab_inicio = tabs[0]
tab_fin = tabs[1]
if es_admin:
    tab_dash = tabs[2]

# --- PESTAÑA 1: INICIO DE TURNO ---
with tab_inicio:
    st.header("Registro de Inicio")
    nombre_inicio = st.text_input("Nombre del Conductor", key="nom_ini")
    km_inicio = st.number_input("Kilometraje Inicial", min_value=0.0, step=0.1, value=None, placeholder="Ej. 12500", key="km_ini")
    
    if st.button("Registrar Inicio de Turno", type="primary"):
        if nombre_inicio and km_inicio is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
            for col in ['Carga del Día', 'Lugar de Carga', 'Comentarios', 'Comprobante']:
                if col not in df_actualizado.columns:
                    df_actualizado[col] = ""
                df_actualizado[col] = df_actualizado[col].astype("object")
            
            nombre_buscado_ini = nombre_inicio.strip().lower()
            turno_abierto = df_actualizado[(df_actualizado['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado_ini) & 
                                           (pd.isna(df_actualizado['Kilometraje Final']) | (df_actualizado['Kilometraje Final'] == ""))]
            
            if not turno_abierto.empty:
                st.error(f"⚠️ Ya tienes un turno iniciado, {nombre_inicio}.")
            else:
                ahora_cdmx = datetime.now(zona_cdmx).strftime("%Y-%m-%d %H:%M:%S")
                nuevo_registro = {
                    'Fecha': ahora_cdmx, 'Nombre': nombre_inicio, 'Kilometraje Inicial': float(km_inicio),
                    'Kilometraje Final': None, 'Total Recorrido': None, 'Carga del Día': None,
                    'Lugar de Carga': None, 'Comentarios': None, 'Comprobante': None
                }
                df_actualizado = pd.concat([df_actualizado, pd.DataFrame([nuevo_registro])], ignore_index=True)
                conn.update(worksheet="Hoja 1", data=df_actualizado)
                st.cache_data.clear()
                st.success(f"✅ ¡Buen viaje, {nombre_inicio}!")
        else:
            st.warning("⚠️ Ingresa nombre y kilometraje.")

# --- PESTAÑA 2: FIN DE TURNO (Soporte para PDF) ---
with tab_fin:
    st.header("Registro Final")
    nombre_fin = st.text_input("Ingresa tu Nombre", key="nom_fin")
    km_fin = st.number_input("Kilometraje Final", min_value=0.0, step=0.1, value=None, placeholder="Ej. 12650", key="km_fin")
    carga_dia = st.text_input("Carga del Día", placeholder="Ej: 123 o 500 + 200", key="carga_dia")
    lugar_carga = st.text_input("Lugar de Carga", key="lugar_carga")
    txt_comentarios = st.text_area("Comentarios (Opcional)", key="coment")
    
    # AJUSTE: Ahora acepta PDF además de imágenes
    archivos_tickets = st.file_uploader("Subir fotos o PDFs de los Tickets", type=["png", "jpg", "jpeg", "pdf"], accept_multiple_files=True)
    
    if st.button("Registrar Fin de Turno", type="primary"):
        if nombre_fin and km_fin is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
            for col in ['Carga del Día', 'Lugar de Carga', 'Comentarios', 'Comprobante']:
                if col not in df_actualizado.columns:
                    df_actualizado[col] = ""
                df_actualizado[col] = df_actualizado[col].astype("object")

            nombre_buscado = nombre_fin.strip().lower()
            pendientes = df_actualizado[(df_actualizado['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado) & 
                            (pd.isna(df_actualizado['Kilometraje Final']) | (df_actualizado['Kilometraje Final'] == ""))]
            
            if not pendientes.empty:
                idx = pendientes.index[-1]
                km_ini = float(df_actualizado.at[idx, 'Kilometraje Inicial'])
                
                if km_fin >= km_ini:
                    total_dinero = calcular_total_carga(carga_dia)
                    links_archivos = []
                    
                    if archivos_tickets:
                        with st.spinner("Subiendo comprobantes (Fotos/PDFs)..."):
                            for archivo in archivos_tickets:
                                url = subir_archivo_a_nube(archivo)
                                if url: links_archivos.append(url)
                    
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
                    
                    st.success(f"🏁 ¡Turno finalizado con éxito, {nombre_fin}!")
                    st.success(f"🚖 Km Recorridos {total_recorrido} km | 🔋 Carga ${total_dinero}")
                    st.balloons() 
                else:
                    st.error(f"❌ El kilometraje final ({km_fin}) no puede ser menor al inicial ({km_ini}).")
            else:
                st.error("❌ No se encontró un turno activo.")
        else:
            st.warning("⚠️ Completa nombre y kilometraje final.")

# --- PESTAÑA 3: DASHBOARD ADMINISTRATIVO ---
if es_admin:
    with tab_dash:
        st.header("Análisis de Operación y Carga")
        
        df_dash = conn.read(worksheet="Hoja 1", ttl=0)

        if not df_dash.empty:
            # 1. Limpieza de datos y Fechas
            df_dash['Fecha'] = pd.to_datetime(df_dash['Fecha'], errors='coerce')
            df_dash = df_dash.dropna(subset=['Fecha'])
            
            if not df_dash.empty:
                df_dash['Semana'] = df_dash['Fecha'].dt.strftime('%Y - Sem %U')
                df_dash['Total Recorrido'] = pd.to_numeric(df_dash['Total Recorrido'], errors='coerce').fillna(0)
                df_dash['Carga del Día'] = pd.to_numeric(df_dash['Carga del Día'], errors='coerce').fillna(0)

                # Selector de semana
                lista_semanas = sorted(df_dash['Semana'].unique(), reverse=True)
                semana_sel = st.selectbox("📅 Selecciona la Semana:", ["Todas"] + lista_semanas)

                # Filtrado
                df_f = df_dash if semana_sel == "Todas" else df_dash[df_dash['Semana'] == semana_sel]

                # --- PROCESAMIENTO DE LUGARES DE CARGA ---
                # Limpiamos los nombres (quitar espacios y poner la primera letra en mayúscula)
                df_f['Lugar de Carga'] = df_f['Lugar de Carga'].astype(str).str.strip().str.title()
                # Quitamos los registros que digan "N/A" o estén vacíos para que no ensucien la gráfica
                df_lugares = df_f[~df_f['Lugar de Carga'].isin(["N/A", "None", "", "Nan"])]
                
                # Contamos cuántas veces aparece cada lugar
                conteo_lugares = df_lugares['Lugar de Carga'].value_counts().reset_index()
                conteo_lugares.columns = ['Lugar', 'Número de Cargas']

                # Agrupación de Gastos por Conductor (para la segunda gráfica)
                resumen_gastos = df_f.groupby('Nombre')['Carga del Día'].sum().reset_index()

                # --- MÉTRICAS SUPERIORES ---
                m1, m2, m3 = st.columns(3)
                m1.metric("KM Totales", f"{df_f['Total Recorrido'].sum():,.1f} km")
                m2.metric("Gasto Total", f"${df_f['Carga del Día'].sum():,.2f}")
                m3.metric("Estaciones Visitadas", len(conteo_lugares))

                st.divider()

                # --- NUEVAS GRÁFICAS ---
                col_g1, col_g2 = st.columns(2)
                
                with col_g1:
                    st.subheader("📍 Lugares más Frecuentados")
                    if not conteo_lugares.empty:
                        # Mostramos qué estaciones se usan más
                        st.bar_chart(data=conteo_lugares, x='Lugar', y='Número de Cargas')
                    else:
                        st.write("No hay datos de lugares en este periodo.")

                with col_g2:
                    st.subheader("💰 Gasto por Conductor")
                    st.bar_chart(data=resumen_gastos, x='Nombre', y='Carga del Día')

                # Tabla de inspección
                with st.expander("Ver detalle de ubicaciones"):
                    st.dataframe(conteo_lugares, use_container_width=True)
            else:
                st.info("No hay fechas válidas para analizar.")
        else:
            st.info("Aún no hay datos en el sistema.")

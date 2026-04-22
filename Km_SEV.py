import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import requests
import base64
import re
from streamlit_gsheets import GSheetsConnection

# --- CONFIGURACIÓN DE IMGBB ---
IMGBB_API_KEY = "c6fc780c833003fcc0583442a0c61f2b" 

st.set_page_config(page_title="Control de Flotilla", layout="centered")

# --- FUNCIONES DE APOYO ---
def subir_a_imgbb(file_obj):
    try:
        url = "https://api.imgbb.com/1/upload"
        imagen_codificada = base64.b64encode(file_obj.read()).decode('utf-8')
        payload = {"key": IMGBB_API_KEY, "image": imagen_codificada}
        res = requests.post(url, data=payload)
        return res.json()['data']['url'] if res.status_code == 200 else None
    except:
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

# Verificamos si el enlace de internet contiene el código secreto "?jefe=true"
if "jefe" in st.query_params and st.query_params["jefe"] == "true":
    with st.sidebar:
        st.title("🔐 Zona Segura")
        st.write("Panel de Control SEV")
        password_admin = st.text_input("Ingresa tu PIN", type="password")
        if password_admin == "admin123":
            es_admin = True
            st.success("Acceso concedido")

st.write("") # Un pequeño espacio en blanco

# Conexión a base de datos
conn = st.connection("gsheets", type=GSheetsConnection)
zona_cdmx = pytz.timezone('America/Mexico_City')

# --- CONFIGURACIÓN DINÁMICA DE PESTAÑAS ---
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
    st.write("Completa esta sección al comenzar tu turno.")
    
    nombre_inicio = st.text_input("Nombre del Conductor", key="nom_ini")
    km_inicio = st.number_input("Kilometraje Inicial", min_value=0.0, step=0.1, value=None, placeholder="Ej. 12500", key="km_ini")
    
    if st.button("Registrar Inicio de Turno", type="primary"):
        if nombre_inicio and km_inicio is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
            # --- BLINDAJE DE COLUMNAS ---
            for col in ['Carga del Día', 'Lugar de Carga', 'Comentarios', 'Comprobante']:
                if col not in df_actualizado.columns:
                    df_actualizado[col] = ""
                df_actualizado[col] = df_actualizado[col].astype("object")
            
            # Verificación de turno ya abierto
            nombre_buscado_ini = nombre_inicio.strip().lower()
            turno_abierto = df_actualizado[(df_actualizado['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado_ini) & 
                                           (pd.isna(df_actualizado['Kilometraje Final']) | (df_actualizado['Kilometraje Final'] == ""))]
            
            if not turno_abierto.empty:
                st.error(f"⚠️ Ya tienes un turno iniciado, {nombre_inicio}. Debes cerrar el anterior primero.")
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
                st.success(f"✅ Inicio registrado ¡Buen Viaje, {nombre_inicio}!")
        else:
            st.warning("⚠️ Ingresa nombre y kilometraje.")

# --- PESTAÑA 2: FIN DE TURNO ---
with tab_fin:
    st.header("Registro Final")
    nombre_fin = st.text_input("Ingresa tu Nombre", key="nom_fin")
    km_fin = st.number_input("Kilometraje Final", min_value=0.0, step=0.1, value=None, placeholder="Ej. 12650", key="km_fin")
    carga_dia = st.text_input("Carga del Día", placeholder="Ej: 123 o 500 + 200", key="carga_dia")
    lugar_carga = st.text_input("Lugar de Carga", key="lugar_carga")
    txt_comentarios = st.text_area("Comentarios (Opcional)", key="coment")
    archivos_tickets = st.file_uploader("Subir fotos de los Tickets", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
    
    if st.button("Registrar Fin de Turno", type="primary"):
        if nombre_fin and km_fin is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
            # --- BLINDAJE DE COLUMNAS ---
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
                    links_tickets = []
                    
                    if archivos_tickets:
                        with st.spinner("Subiendo comprobantes..."):
                            for archivo in archivos_tickets:
                                url = subir_a_imgbb(archivo)
                                if url: links_tickets.append(url)
                    
                    link_final = " ".join(links_tickets) if links_tickets else "No subido"
                    total_recorrido = float(km_fin - km_ini)

                    # Guardado en Excel
                    df_actualizado.at[idx, 'Kilometraje Final'] = float(km_fin)
                    df_actualizado.at[idx, 'Total Recorrido'] = total_recorrido
                    df_actualizado.at[idx, 'Carga del Día'] = total_dinero
                    df_actualizado.at[idx, 'Lugar de Carga'] = str(lugar_carga) if lugar_carga else "N/A"
                    df_actualizado.at[idx, 'Comentarios'] = str(txt_comentarios) if txt_comentarios else ""
                    df_actualizado.at[idx, 'Comprobante'] = link_final
                    
                    conn.update(worksheet="Hoja 1", data=df_actualizado)
                    st.cache_data.clear()
                    
                    # --- RESUMEN VISUAL PARA EL CONDUCTOR ---
                    st.success(f"🏁¡Turno finalizado con éxito, {nombre_fin}!")
                    st.success(f"🚖 Km Recorridos {total_recorrido} km | 🔋 Carga ${total_dinero}")
                    st.balloons() 
                else:
                    st.error(f"❌ El kilometraje final ({km_fin}) no puede ser menor al inicial ({km_ini}).")
            else:
                st.error("❌ No se encontró un turno activo. Verifica tu nombre.")
        else:
            st.warning("⚠️ Completa nombre y kilometraje final.")

# --- PESTAÑA 3: DASHBOARD ADMINISTRATIVO OCULTO ---
if es_admin:
    with tab_dash:
        st.header("Análisis Semanal de Flotilla")
        
        # Leemos el Excel para graficar
        df_dash = conn.read(worksheet="Hoja 1", ttl=0)

        if not df_dash.empty:
            # Limpieza y conversión de fechas
            df_dash['Fecha'] = pd.to_datetime(df_dash['Fecha'], errors='coerce')
            df_dash = df_dash.dropna(subset=['Fecha']) # Quitamos errores o cortes
            
            if not df_dash.empty:
                # Crear columna de Semana
                df_dash['Semana'] = df_dash['Fecha'].dt.strftime('%Y - Sem %U')
                
                # Asegurar que sean números
                df_dash['Total Recorrido'] = pd.to_numeric(df_dash['Total Recorrido'], errors='coerce').fillna(0)
                df_dash['Carga del Día'] = pd.to_numeric(df_dash['Carga del Día'], errors='coerce').fillna(0)

                # Selector interactivo de semana
                lista_semanas = sorted(df_dash['Semana'].unique(), reverse=True)
                semana_seleccionada = st.selectbox("📅 Selecciona la Semana a consultar:", ["Todas"] + lista_semanas)

                # Filtrar base de datos
                if semana_seleccionada != "Todas":
                    df_filtrado = df_dash[df_dash['Semana'] == semana_seleccionada]
                else:
                    df_filtrado = df_dash

                # Agrupar datos por conductor
                resumen = df_filtrado.groupby('Nombre').agg({
                    'Total Recorrido': 'sum',
                    'Carga del Día': 'sum'
                }).reset_index()

                # Mostrar Métricas Totales
                m1, m2 = st.columns(2)
                m1.metric(f"KM Totales ({semana_seleccionada})", f"{df_filtrado['Total Recorrido'].sum():,.1f} km")
                m2.metric(f"Gasto Total ({semana_seleccionada})", f"${df_filtrado['Carga del Día'].sum():,.2f}")

                st.divider()

                # Mostrar Gráficas de Barras
                col_g1, col_g2 = st.columns(2)
                with col_g1:
                    st.subheader("Distancia por Conductor")
                    st.bar_chart(data=resumen, x='Nombre', y='Total Recorrido')
                with col_g2:
                    st.subheader("Gasto de Carga")
                    st.bar_chart(data=resumen, x='Nombre', y='Carga del Día')
            else:
                st.info("No hay fechas válidas para analizar aún.")
        else:
            st.info("Aún no hay datos para mostrar en el dashboard.")

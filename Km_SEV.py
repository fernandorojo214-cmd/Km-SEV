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
# Reemplaza con tu clave de api.imgbb.com
IMGBB_API_KEY = "c6fc780c833003fcc0583442a0c61f2b" 

st.set_page_config(page_title="SEVTrack | Control de Flotilla", layout="centered")

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
    # Título con ajuste de altura para alinearse al logo
    st.markdown("<h1 style='margin-top: 25px;'>Control de Flotilla SEV</h1>", unsafe_allow_html=True)

st.divider()

# Conexión a base de datos
conn = st.connection("gsheets", type=GSheetsConnection)
zona_cdmx = pytz.timezone('America/Mexico_City')

tab_inicio, tab_fin = st.tabs(["🟢 Iniciar Turno", "🔴 Finalizar Turno"])

# --- PESTAÑA 1: INICIO DE TURNO (Con Candado Anti-Duplicados) ---
with tab_inicio:
    st.header("Registro de Inicio")
    st.write("Completa esta sección al comenzar tu turno.")
    
    nombre_inicio = st.text_input("Nombre del Conductor", key="nom_ini")
    km_inicio = st.number_input("Kilometraje Inicial", min_value=0.0, step=0.1, value=None, placeholder="Ej. 12500.5", key="km_ini")
    
    if st.button("Registrar Inicio de Turno", type="primary"):
        if nombre_inicio and km_inicio is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
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

# --- PESTAÑA 2: FIN DE TURNO (Con Resumen de Desempeño) ---
with tab_fin:
    st.header("Registro Final")
    nombre_fin = st.text_input("Ingresa tu Nombre", key="nom_fin")
    km_fin = st.number_input("Kilometraje Final", min_value=0.0, step=0.1, value=None, placeholder="Ej. 12650.0", key="km_fin")
    carga_dia = st.text_input("Carga del Día (Suma automática)", placeholder="Ej: 500 + 200", key="carga_dia")
    lugar_carga = st.text_input("Lugar de Carga", key="lugar_carga")
    txt_comentarios = st.text_area("Comentarios (Opcional)", key="coment")
    archivos_tickets = st.file_uploader("Subir fotos de los Tickets", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
    
    if st.button("Registrar Fin de Turno", type="primary"):
        if nombre_fin and km_fin is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
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

                    # Guardado en Excel (Valores numéricos limpios)
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
                    
                
                    
                    st.balloons() # Animación de celebración opcional
                else:
                    st.error(f"❌ El kilometraje final ({km_fin}) no puede ser menor al inicial ({km_ini}).")
            else:
                st.error("❌ No se encontró un turno activo. Verifica tu nombre.")
        else:
            st.warning("⚠️ Completa nombre y kilometraje final.")

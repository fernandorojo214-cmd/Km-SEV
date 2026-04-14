import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import requests
import base64
from streamlit_gsheets import GSheetsConnection

# --- CONFIGURACIÓN DE IMGBB ---
IMGBB_API_KEY = "c6fc780c833003fcc0583442a0c61f2b" 

# Configuración de la página
st.set_page_config(page_title="Control de Flotilla", layout="centered")

# --- FUNCIÓN PARA SUBIR FOTO DEL TICKET ---
def subir_a_imgbb(file_obj):
    try:
        url = "https://api.imgbb.com/1/upload"
        # Convertimos la imagen a un formato que el servidor pueda leer
        imagen_codificada = base64.b64encode(file_obj.read()).decode('utf-8')
        payload = {
            "key": IMGBB_API_KEY,
            "image": imagen_codificada
        }
        res = requests.post(url, data=payload)
        
        if res.status_code == 200:
            # Si fue exitoso, extraemos el enlace (link) de la foto
            return res.json()['data']['url']
        else:
            return None
    except Exception as e:
        st.error(f"Error al subir imagen: {e}")
        return None

# --- SECCIÓN DEL LOGO Y TÍTULO ---
col1, col2 = st.columns([1, 4])

with col1:
    try:
        if os.path.exists("logo.png"):
            st.image("logo.png", width=200)
    except Exception:
        pass

with col2:
    st.markdown("<h1 style='margin-top: 25px;'>Control de Flotilla SEV</h1>", unsafe_allow_html=True)

st.divider()

# Conectar con Google Sheets
conn = st.connection("gsheets", type=GSheetsConnection)
df = conn.read(worksheet="Hoja 1", ttl=0)

# --- ACTUALIZACIÓN SEGURA DE COLUMNAS ---
columnas_esperadas = ['Fecha', 'Nombre', 'Kilometraje Inicial', 'Kilometraje Final', 'Total Recorrido', 'Carga del Día', 'Lugar de Carga', 'Comentarios', 'Comprobante']
necesita_actualizar_columnas = False

for col in columnas_esperadas:
    if col not in df.columns:
        df[col] = None
        necesita_actualizar_columnas = True

if necesita_actualizar_columnas:
    df = df[columnas_esperadas]
    conn.update(worksheet="Hoja 1", data=df)

zona_cdmx = pytz.timezone('America/Mexico_City')

tab_inicio, tab_fin = st.tabs(["🟢 Iniciar Turno", "🔴 Finalizar Turno"])

# --- PESTAÑA 1: INICIO DE TURNO ---
with tab_inicio:
    st.header("Registro de Inicio")
    st.write("Completa esta sección al comenzar tu turno de servicio.")
    
    nombre_inicio = st.text_input("Nombre del Conductor", key="nom_ini")
    km_inicio = st.number_input("Kilometraje Inicial", min_value=0.0, step=0.1, value=None, placeholder="Ej. 12500", key="km_ini")
    
    if st.button("Registrar Inicio de Turno", type="primary"):
        if nombre_inicio and km_inicio is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
            for col in ['Carga del Día', 'Lugar de Carga', 'Comentarios', 'Comprobante']:
                if col in df_actualizado.columns:
                    df_actualizado[col] = df_actualizado[col].astype(object)
            
            ahora_cdmx = datetime.now(zona_cdmx)
            hora_actual_str = ahora_cdmx.strftime("%Y-%m-%d %H:%M:%S")
            
            fechas_validas = df_actualizado[df_actualizado['Fecha'] != '---']['Fecha'].dropna()
            
            if not fechas_validas.empty:
                ultima_fecha_str = fechas_validas.iloc[-1]
                ultima_fecha = pd.to_datetime(ultima_fecha_str, errors='coerce')
                
                if pd.notna(ultima_fecha):
                    if ultima_fecha.tzinfo is None:
                        ultima_fecha = zona_cdmx.localize(ultima_fecha)
                    
                    if ultima_fecha.isocalendar()[1] != ahora_cdmx.isocalendar()[1] or ultima_fecha.year != ahora_cdmx.year:
                        fila_corte = {
                            'Fecha': '---', 'Nombre': '--- CORTE DE SEMANA ---', 'Kilometraje Inicial': None,
                            'Kilometraje Final': None, 'Total Recorrido': None, 'Carga del Día': '---',
                            'Lugar de Carga': '---', 'Comentarios': '---', 'Comprobante': '---'
                        }
                        df_actualizado = pd.concat([df_actualizado, pd.DataFrame([fila_corte])], ignore_index=True)
            
            nuevo_registro = {
                'Fecha': hora_actual_str, 'Nombre': nombre_inicio, 'Kilometraje Inicial': float(km_inicio),
                'Kilometraje Final': None, 'Total Recorrido': None, 'Carga del Día': None,
                'Lugar de Carga': None, 'Comentarios': None, 'Comprobante': None
            }
            
            df_actualizado = pd.concat([df_actualizado, pd.DataFrame([nuevo_registro])], ignore_index=True)
            conn.update(worksheet="Hoja 1", data=df_actualizado)
            st.cache_data.clear()
            st.success(f"✅ ¡Buen turno, {nombre_inicio}! Registro guardado.")
        else:
            st.warning("⚠️ Por favor, ingresa tu nombre y el kilometraje inicial.")

# --- PESTAÑA 2: FIN DE TURNO ---
with tab_fin:
    st.header("Registro Final")
    st.write("Completa esta sección al finalizar tu turno.")
    
    nombre_fin = st.text_input("Ingresa tu Nombre", key="nom_fin")
    km_fin = st.number_input("Kilometraje Final", min_value=0.0, step=0.1, value=None, placeholder="Ej. 12650", key="km_fin")
    carga_dia = st.text_input("Carga del Día (Ej. $500)", key="carga_dia")
    lugar_carga = st.text_input("Lugar de Carga (Ej. Gran Oso, Roma, etc.)", key="lugar_carga")
    txt_comentarios = st.text_area("Comentarios (Opcional)", key="coment")
    
    # Campo para subir la imagen (Limitado solo a fotos, no PDFs)
    archivo_ticket = st.file_uploader("Subir foto recibo", type=["png", "jpg", "jpeg"])
    
    if st.button("Registrar Fin de Turno", type="primary"):
        if nombre_fin and km_fin is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
            for col in ['Carga del Día', 'Lugar de Carga', 'Comentarios', 'Comprobante']:
                if col in df_actualizado.columns:
                    df_actualizado[col] = df_actualizado[col].astype(object)
                
            nombre_buscado = nombre_fin.strip().lower()
            pendientes = df_actualizado[(df_actualizado['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado) & 
                            (pd.isna(df_actualizado['Kilometraje Final']) | (df_actualizado['Kilometraje Final'] == ""))]
            
            if not pendientes.empty:
                idx = pendientes.index[-1]
                km_ini = float(df_actualizado.at[idx, 'Kilometraje Inicial'])
                
                if km_fin >= km_ini:
                    # --- LÓGICA DE SUBIDA DE IMAGEN A IMGBB ---
                    link_ticket = "No subido"
                    if archivo_ticket is not None:
                        with st.spinner("Subiendo recibo..."):
                            url_foto = subir_a_imgbb(archivo_ticket)
                            if url_foto:
                                link_ticket = url_foto

                    total_recorrido = float(km_fin - km_ini)
                    df_actualizado.at[idx, 'Kilometraje Final'] = float(km_fin)
                    df_actualizado.at[idx, 'Total Recorrido'] = total_recorrido
                    df_actualizado.at[idx, 'Carga del Día'] = str(carga_dia) if carga_dia else "0"
                    df_actualizado.at[idx, 'Lugar de Carga'] = str(lugar_carga) if lugar_carga else "N/A"
                    df_actualizado.at[idx, 'Comentarios'] = str(txt_comentarios) if txt_comentarios else ""
                    df_actualizado.at[idx, 'Comprobante'] = link_ticket
                    
                    conn.update(worksheet="Hoja 1", data=df_actualizado)
                    st.cache_data.clear()
                    
                    st.success(f"🏁 Turno finalizado para {df_actualizado.at[idx, 'Nombre']}.")
                    st.info(f"📊 Recorrido: **{total_recorrido:.1f} km**")
                else:
                    st.error("❌ El KM final no puede ser menor al inicial.")
            else:
                st.error("❌ No se encontró un turno activo con ese nombre.")
        else:
            st.warning("⚠️ Ingresa nombre y kilometraje final.")

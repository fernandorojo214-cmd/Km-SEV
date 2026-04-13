import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
from streamlit_gsheets import GSheetsConnection

# Configuración de la página
st.set_page_config(page_title="Control de Flotilla", layout="centered")

# --- SECCIÓN DEL LOGO Y TÍTULO ---
col1, col2 = st.columns([1, 4])

with col1:
    try:
        if os.path.exists("logo.png"):
            st.image("logo.png", width=80)
    except Exception:
        pass

with col2:
    # Puedes cambiar "SEVTrack" por el nombre que hayas elegido
    st.markdown("<h1 style='margin-top: -15px;'>SEVTrack | Control de Flotilla</h1>", unsafe_allow_html=True)

st.divider()

# Conectar con Google Sheets
conn = st.connection("gsheets", type=GSheetsConnection)
df = conn.read(worksheet="Hoja 1", ttl=0)

# --- ACTUALIZACIÓN SEGURA DE COLUMNAS ---
columnas_esperadas = ['Fecha', 'Nombre', 'Kilometraje Inicial', 'Kilometraje Final', 'Total Recorrido', 'Carga del Día', 'Lugar de Carga']
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
    
    # AJUSTE: value=None y placeholder añadidos para que aparezca vacío
    km_inicio = st.number_input("Kilometraje Inicial", min_value=0.0, step=0.1, value=None, placeholder="Ej. 12500.5", key="km_ini")
    
    if st.button("Registrar Inicio de Turno", type="primary"):
        # AJUSTE: Verificamos que ambos campos tengan información
        if nombre_inicio and km_inicio is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
            if 'Carga del Día' in df_actualizado.columns:
                df_actualizado['Carga del Día'] = df_actualizado['Carga del Día'].astype(object)
            if 'Lugar de Carga' in df_actualizado.columns:
                df_actualizado['Lugar de Carga'] = df_actualizado['Lugar de Carga'].astype(object)
            
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
                            'Fecha': '---',
                            'Nombre': '--- CORTE DE SEMANA ---',
                            'Kilometraje Inicial': None,
                            'Kilometraje Final': None,
                            'Total Recorrido': None,
                            'Carga del Día': '---',
                            'Lugar de Carga': '---'
                        }
                        df_actualizado = pd.concat([df_actualizado, pd.DataFrame([fila_corte])], ignore_index=True)
            
            nuevo_registro = {
                'Fecha': hora_actual_str,
                'Nombre': nombre_inicio,
                'Kilometraje Inicial': float(km_inicio),
                'Kilometraje Final': None,
                'Total Recorrido': None,
                'Carga del Día': None,
                'Lugar de Carga': None
            }
            
            df_actualizado = pd.concat([df_actualizado, pd.DataFrame([nuevo_registro])], ignore_index=True)
            conn.update(worksheet="Hoja 1", data=df_actualizado)
            
            st.cache_data.clear()
            st.success(f"✅ ¡Buen turno, {nombre_inicio}! Kilometraje inicial guardado.")
        else:
            st.warning("⚠️ Por favor, ingresa tu nombre y el kilometraje inicial válido.")

# --- PESTAÑA 2: FIN DE TURNO ---
with tab_fin:
    st.header("Registro Final")
    st.write("Completa esta sección al finalizar tu turno.")
    
    nombre_fin = st.text_input("Ingresa tu Nombre", key="nom_fin")
    
    # AJUSTE: value=None y placeholder añadidos
    km_fin = st.number_input("Kilometraje Final", min_value=0.0, step=0.1, value=None, placeholder="Ej. 12650.0", key="km_fin")
    
    carga_dia = st.text_input("Carga del Día (Ej. $500, 20 Lts, etc.)", key="carga_dia")
    lugar_carga = st.text_input("Lugar de Carga (Ej. Gasolinera Centro, Sucursal Sur, etc.)", key="lugar_carga")
    
    if st.button("Registrar Fin de Turno", type="primary"):
        # AJUSTE: Verificamos que ambos campos obligatorios tengan información
        if nombre_fin and km_fin is not None:
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
            if 'Carga del Día' in df_actualizado.columns:
                df_actualizado['Carga del Día'] = df_actualizado['Carga del Día'].astype(object)
            if 'Lugar de Carga' in df_actualizado.columns:
                df_actualizado['Lugar de Carga'] = df_actualizado['Lugar de Carga'].astype(object)
                
            nombre_buscado = nombre_fin.strip().lower()
            
            pendientes = df_actualizado[(df_actualizado['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado) & 
                            (pd.isna(df_actualizado['Kilometraje Final']) | (df_actualizado['Kilometraje Final'] == ""))]
            
            if not pendientes.empty:
                idx = pendientes.index[-1]
                try:
                    km_ini = float(df_actualizado.at[idx, 'Kilometraje Inicial'])
                except:
                    km_ini = 0.0 
                
                if km_fin >= km_ini:
                    total_recorrido = float(km_fin - km_ini)
                    df_actualizado.at[idx, 'Kilometraje Final'] = float(km_fin)
                    df_actualizado.at[idx, 'Total Recorrido'] = total_recorrido
                    df_actualizado.at[idx, 'Carga del Día'] = str(carga_dia) if carga_dia else "0"
                    df_actualizado.at[idx, 'Lugar de Carga'] = str(lugar_carga) if lugar_carga else "N/A"
                    
                    conn.update(worksheet="Hoja 1", data=df_actualizado)
                    
                    st.cache_data.clear()
                    
                    nombre_original = df_actualizado.at[idx, 'Nombre']
                    st.success(f"🏁 Fin de turno registrado para {nombre_original}.")
                    st.info(f"📊 Recorriste: **{total_recorrido:.1f} km** | Carga: **{carga_dia if carga_dia else '0'}** en **{lugar_carga if lugar_carga else 'Ninguno'}**")
                else:
                    st.error(f"❌ El kilometraje final ({km_fin}) no puede ser menor al inicial ({km_ini}). Verifica tus datos.")
            else:
                st.error("❌ No se encontró un turno activo con ese nombre. Verifica que esté escrito igual que al inicio.")
        else:
            st.warning("⚠️ Por favor, ingresa tu nombre y el kilometraje final válido para registrar tu salida.")

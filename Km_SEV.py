import streamlit as st
import pandas as pd
from datetime import datetime
from streamlit_gsheets import GSheetsConnection

# Configuración de la página
st.set_page_config(page_title="Control de Flotilla", layout="centered")
st.title("🚗 Control de Kilometraje - Flotilla")

# Conectar con Google Sheets
conn = st.connection("gsheets", type=GSheetsConnection)

# El parámetro ttl=0 obliga a Streamlit a NO usar la memoria caché y leer siempre en vivo
df = conn.read(worksheet="Hoja 1", ttl=0)

# Validar la estructura de las columnas
if df.columns.tolist() != ['Fecha', 'Nombre', 'Kilometraje Inicial', 'Kilometraje Final', 'Total Recorrido']:
     df = pd.DataFrame(columns=['Fecha', 'Nombre', 'Kilometraje Inicial', 'Kilometraje Final', 'Total Recorrido'])

tab_inicio, tab_fin = st.tabs(["🟢 Iniciar Turno", "🔴 Finalizar Turno"])

# --- PESTAÑA 1: INICIO DE TURNO ---
with tab_inicio:
    st.header("Registro de Inicio")
    st.write("Completa esta sección al comenzar tu turno de servicio.")
    
    nombre_inicio = st.text_input("Nombre del Conductor", key="nom_ini")
    km_inicio = st.number_input("Kilometraje Inicial", min_value=0.0, step=0.1, key="km_ini")
    
    if st.button("Registrar Inicio de Turno", type="primary"):
        if nombre_inicio:
            # Volvemos a leer los datos más frescos justo antes de escribir
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
            nuevo_registro = {
                'Fecha': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'Nombre': nombre_inicio,
                'Kilometraje Inicial': km_inicio,
                'Kilometraje Final': None,
                'Total Recorrido': None
            }
            
            # Añadimos la fila a los datos frescos y actualizamos
            df_actualizado = pd.concat([df_actualizado, pd.DataFrame([nuevo_registro])], ignore_index=True)
            conn.update(worksheet="Hoja 1", data=df_actualizado)
            
            # Limpiamos cualquier memoria residual
            st.cache_data.clear()
            
            st.success(f"✅ ¡Buen turno, {nombre_inicio}! Kilometraje inicial guardado.")
        else:
            st.warning("⚠️ Por favor, ingresa tu nombre.")

# --- PESTAÑA 2: FIN DE TURNO ---
with tab_fin:
    st.header("Registro Final")
    st.write("Completa esta sección al finalizar tu turno.")
    
    nombre_fin = st.text_input("Ingresa tu Nombre", key="nom_fin")
    km_fin = st.number_input("Kilometraje Final", min_value=0.0, step=0.1, key="km_fin")
    
    if st.button("Registrar Fin de Turno", type="primary"):
        if nombre_fin:
            # Volvemos a leer los datos más frescos JUSTO ANTES de hacer la resta para no borrar a nadie
            df_actualizado = conn.read(worksheet="Hoja 1", ttl=0)
            
            nombre_buscado = nombre_fin.strip().lower()
            
            pendientes = df_actualizado[(df_actualizado['Nombre'].astype(str).str.strip().str.lower() == nombre_buscado) & 
                            (pd.isna(df_actualizado['Kilometraje Final']) | (df_actualizado['Kilometraje Final'] == ""))]
            
            if not pendientes.empty:
                idx = pendientes.index[-1]
                km_ini = float(df_actualizado.at[idx, 'Kilometraje Inicial'])
                
                if km_fin >= km_ini:
                    total_recorrido = km_fin - km_ini
                    df_actualizado.at[idx, 'Kilometraje Final'] = km_fin
                    df_actualizado.at[idx, 'Total Recorrido'] = total_recorrido
                    
                    # Actualizamos Google Sheets con la tabla correcta
                    conn.update(worksheet="Hoja 1", data=df_actualizado)
                    
                    # Limpiamos memoria residual
                    st.cache_data.clear()
                    
                    nombre_original = df_actualizado.at[idx, 'Nombre']
                    st.success(f"🏁 Fin de turno registrado para {nombre_original}. Total recorrido: **{total_recorrido:.1f} km**.")
                else:
                    st.error(f"❌ El kilometraje final ({km_fin}) no puede ser menor al inicial ({km_ini}). Verifica tus datos.")
            else:
                st.error("❌ No se encontró un turno activo con ese nombre. Verifica que esté escrito igual que al inicio.")
        else:
            st.warning("⚠️ Por favor, ingresa tu nombre para finalizar.")

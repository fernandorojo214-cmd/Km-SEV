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
    
    st.write("") 
    
    # ⚙️ CONTROL MAESTRO DE TAMAÑO Y CENTRADO
    PROPORCION_COLUMNAS = [1, 1.2, 1]  

    c_izq, c_centro, c_der = st.columns(PROPORCION_COLUMNAS)
    
    with c_centro:
        if os.path.exists("logo.png"):
            st.image("logo.png", use_container_width=True)
    
    st.markdown("<h2 style='text-align: center; margin-top: -10px; margin-bottom: 10px;'>SOLAR FLEET</h2>", unsafe_allow_html=True)
    st.divider()

    # 1. PANTALLA DE LOGIN NORMAL
    if st.session_state['pantalla_auth'] == 'login':
        usuario = st.text_input("Usuario (Ej. frojo)").strip().lower()
        password = st.text_input("Contraseña", type="password")
        
        if st.button("Iniciar Sesión", type="primary", use_container_width=True):
            if usuario in st.session_state['db_usuarios'] and st.session_state['db_usuarios'][usuario] == password:
                st.session_state['logged_in'] = True
                st.session_state['current_user'] = usuario.title() 
                st.rerun() 
            else:
                st.error("❌ Usuario o contraseña incorrectos.")
        
        st.write("")
        c1, c2 = st.columns(2)
        c1.button("Crear cuenta", on_click=ir_a_registro, use_container_width=True)
        c2.button("¿Olvidaste tu clave?", on_click=ir_a_recuperar, use_container_width=True)

    # 2. PANTALLA DE REGISTRO (GENERADOR AUTOMÁTICO)
    elif st.session_state['pantalla_auth'] == 'registro':
        st.subheader("Crear nueva cuenta")
        nombre = st.text_input("Nombre (Ej. Fernando)").strip().lower()
        apellido = st.text_input("Apellido (Ej. Rojo)").strip().lower() 
        nueva_pass = st.text_input("Crea una contraseña", type="password")
        pass_conf = st.text_input("Confirma tu contraseña", type="password")
        
        if st.button("Registrarse", type="primary", use_container_width=True):
            if not nombre or not apellido:
                st.error("⚠️ Por favor, ingresa tu nombre y apellido.")
            elif nueva_pass != pass_conf:
                st.error("❌ Las contraseñas no coinciden.")
            elif len(nueva_pass) < 4:
                st.error("⚠️ La contraseña debe tener al menos 4 caracteres.")
            else:
                # Lógica para crear el usuario
                base_usr = f"{nombre[0]}{apellido}".replace(" ", "")
                nuevo_usr = base_usr
                contador = 1
                
                while nuevo_usr in st.session_state['db_usuarios']:
                    nuevo_usr = f"{base_usr}{contador}"
                    contador += 1
                    
                st.session_state['db_usuarios'][nuevo_usr] = nueva_pass
                
                st.success("✅ ¡Cuenta creada exitosamente!")
                st.info(f"👤 Tu usuario generado es: **{nuevo_usr}**")
                st.warning("⚠️ IMPORTANTE: Anota este usuario para poder iniciar sesión.")
        
        st.button("🔙 Volver al Login", on_click=ir_a_login, use_container_width=True)

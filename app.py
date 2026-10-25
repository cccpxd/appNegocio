import os
import json
import streamlit as st
from PIL import Image
from dotenv import load_dotenv
from google import genai

# 1. Cargar variables de entorno desde el archivo .env
load_dotenv()

# 2. Configurar la clave de la API de Gemini
api_key = os.getenv("GOOGLE_API_KEY")

# Si no está en el .env, pégala manualmente entre comillas aquí abajo:
if not api_key:
    api_key = "TU_API_KEY_DE_GOOGLE_AI_STUDIO"

# 3. Inicializar el cliente oficial con tu API Key
client = genai.Client(api_key=api_key)

# 4. Configuración de la interfaz en Streamlit
st.set_page_config(page_title="Lector de Facturas con IA", page_icon="🧾", layout="centered")

st.title("🧾 Extractor de Datos de Facturas")
st.write("Sube la foto o imagen de tu factura para extraer sus datos estructurados en JSON listo para base de datos.")

# Cargar la imagen desde la interfaz
uploaded_file = st.file_uploader("Selecciona una imagen de la factura...", type=["jpg", "jpeg", "png"])

if uploaded_file is not None:
    # Mostrar la imagen cargada
    image = Image.open(uploaded_file)
    st.image(image, caption="Factura Subida", use_column_width=True)

    # Prompt optimizado para formato JSON
    prompt_base = """
    Analiza la imagen de esta factura o recibo y extrae la información requerida.
    Debes responder ÚNICAMENTE con un objeto JSON válido, sin bloques de código markdown, sin prefijos y sin texto adicional.

    Usa la siguiente estructura JSON exactamente:
    {
      "proveedor": "Nombre de la empresa o negocio",
      "nit_rut": "Número de identificación fiscal",
      "fecha": "YYYY-MM-DD",
      "subtotal": 0.0,
      "iva": 0.0,
      "total": 0.0,
      "items": [
        {
          "descripcion": "Nombre del producto/servicio",
          "cantidad": 0,
          "precio_unitario": 0.0,
          "total_item": 0.0
        }
      ]
    }
    """

    custom_prompt = st.text_area("Instrucciones adicionales para la IA (opcional):", value="")

    if st.button("Procesar Factura"):
        with st.spinner("Leyendo la factura con Gemini 3.8 Flash..."):
            try:
                final_prompt = prompt_base if not custom_prompt else f"{prompt_base}\nInstrucción adicional: {custom_prompt}"

                # Llamada usando el modelo gemini-3.8-flash habilitado
                response = client.models.generate_content(
                    model='gemini-3.8-flash',
                    contents=[image, final_prompt]
                )

                st.success("¡Factura procesada con éxito!")
                
                # Mostrar el texto retornado
                st.subheader("Resultado JSON:")
                st.code(response.text, language="json")

                # Parsear el JSON para renderizarlo visualmente
                try:
                    cleaned_json = response.text.strip().replace("```json", "").replace("```", "")
                    json_data = json.loads(cleaned_json)
                    st.subheader("Vista Estructurada:")
                    st.json(json_data)
                except Exception:
                    pass

            except Exception as e:
                st.error(f"Ocurrió un error al procesar la factura: {e}")
# my-english-corrector-backend/llm_services.py
import os
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv() # Asegurarse de que las variables de entorno estén cargadas

# Configuración de API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# --- Configuración de Modelos de VISIÓN ---
DEFAULT_VISION_MODEL_PROVIDER = os.getenv("DEFAULT_VISION_MODEL_PROVIDER", "GEMINI_FLASH")
OPENAI_VISION_MODEL_NAME = os.getenv("OPENAI_VISION_MODEL_NAME", "gpt-4o-mini") # O gpt-4o
GOOGLE_VISION_MODEL_NAME = os.getenv("GOOGLE_VISION_MODEL_NAME", "gemini-1.5-flash-latest")

# --- Configuración de Modelos de LENGUAJE (para Corrección) ---
DEFAULT_LANGUAGE_MODEL_PROVIDER = os.getenv("DEFAULT_LANGUAGE_MODEL_PROVIDER", "OPENAI")
OPENAI_LANGUAGE_MODEL_NAME = os.getenv("OPENAI_LANGUAGE_MODEL_NAME", "gpt-4o")
GOOGLE_LANGUAGE_MODEL_NAME = os.getenv("GOOGLE_LANGUAGE_MODEL_NAME", "gemini-1.5-pro-latest")

# --- Versión del Prompt de Corrección ---
CORRECTION_PROMPT_VERSION_CURRENT = "1.0"


def get_vision_model_client():
    """
    Retorna una instancia del cliente LLM de visión configurado
    basado en DEFAULT_VISION_MODEL_PROVIDER.
    """
    if DEFAULT_VISION_MODEL_PROVIDER == "GEMINI_FLASH":
        if not GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY no está configurada para el modelo de visión de Google.")
        print(f"Usando modelo de visión de Google: {GOOGLE_VISION_MODEL_NAME}")
        return ChatGoogleGenerativeAI(model=GOOGLE_VISION_MODEL_NAME, google_api_key=GOOGLE_API_KEY)
    
    elif DEFAULT_VISION_MODEL_PROVIDER == "GPT4O_MINI": 
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY no está configurada para el modelo de visión de OpenAI.")
        print(f"Usando modelo de visión de OpenAI: {OPENAI_VISION_MODEL_NAME}")
        return ChatOpenAI(model=OPENAI_VISION_MODEL_NAME, openai_api_key=OPENAI_API_KEY)
    
    else:
        raise ValueError(f"Proveedor de modelo de visión no soportado: {DEFAULT_VISION_MODEL_PROVIDER}")


def get_language_model_client():
    """
    Retorna una instancia del cliente LLM de lenguaje configurado
    basado en DEFAULT_LANGUAGE_MODEL_PROVIDER.
    """
    if DEFAULT_LANGUAGE_MODEL_PROVIDER == "GOOGLE":
        if not GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY no está configurada para el modelo de lenguaje de Google.")
        print(f"Usando modelo de lenguaje de Google: {GOOGLE_LANGUAGE_MODEL_NAME}")
        return ChatGoogleGenerativeAI(model=GOOGLE_LANGUAGE_MODEL_NAME, google_api_key=GOOGLE_API_KEY,
                                      temperature=0.3, top_p=0.9)
    
    elif DEFAULT_LANGUAGE_MODEL_PROVIDER == "OPENAI":
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY no está configurada para el modelo de lenguaje de OpenAI.")
        print(f"Usando modelo de lenguaje de OpenAI: {OPENAI_LANGUAGE_MODEL_NAME}")
        return ChatOpenAI(model=OPENAI_LANGUAGE_MODEL_NAME, openai_api_key=OPENAI_API_KEY,
                          temperature=0.3, top_p=0.9)
    
    else:
        raise ValueError(f"Proveedor de modelo de lenguaje no soportado: {DEFAULT_LANGUAGE_MODEL_PROVIDER}")


async def transcribe_image_url_with_llm(image_url: str, prompt_text: str | None = None) -> str:
    """
    Toma una URL de imagen y un prompt, y usa el LLM de visión configurado
    para obtener una transcripción o descripción.
    El LLM NO debe corregir errores gramaticales o de ortografía, solo transcribir.
    La transcripción DEBE estar en el mismo idioma que el texto manuscrito (asumido inglés).
    """
    llm = get_vision_model_client()

    if prompt_text is None:
        prompt_text = (
            "Transcribe el texto manuscrito visible en esta imagen. "
            "**La transcripción debe estar en el mismo idioma que el texto original en la imagen (se asume que es inglés).** "
            "Transcribe de la forma más precisa y literal posible. "
            "Conserva cualquier error gramatical, de ortografía o de puntuación presente en el texto original. "
            "NO intentes corregir, traducir, ni mejorar el texto del estudiante. "
            "Tu única tarea es transcribir fielmente lo que está escrito, en su idioma original."
        )

    message_content = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    human_message = HumanMessage(content=message_content)
    
    print(f"Enviando imagen {image_url} y prompt de transcripción al LLM...")
    # ... (resto de la función igual que antes) ...
    try:
        ai_response = await llm.ainvoke([human_message]) 
        transcription = str(ai_response.content) if ai_response.content else ""
        print("LLM Transcription Response Content (first 300 chars):", transcription[:300])
        return transcription
    except Exception as e:
        print(f"Error al llamar al LLM de visión para transcripción: {e}")
        raise

async def correct_text_with_llm(text_to_correct: str, student_level: str = "intermediate") -> str:
    """
    Toma un texto (la redacción transcrita) y usa el LLM de lenguaje configurado
    para obtener una corrección y feedback.
    """
    llm = get_language_model_client()
    
    system_prompt_for_correction = """
    Eres un profesor de inglés experto, paciente y alentador, especializado en corregir redacciones de estudiantes.
    Tu objetivo es ayudar al estudiante a mejorar sus habilidades de escritura en inglés.
    Revisa la redacción que te proporcionará el usuario.
    Debes proporcionar un feedback estructurado y constructivo. Sigue este formato EXACTO:

    **Feedback General:**
    (Un breve resumen de tu impresión general sobre la redacción. Comienza con algo positivo si es posible.)

    **Puntos Fuertes:**
    (Enumera 2-3 aspectos positivos de la redacción. Ej: buen uso de vocabulario específico, ideas bien organizadas, argumento claro, etc.)
    - Ejemplo punto fuerte 1
    - Ejemplo punto fuerte 2

    **Áreas de Mejora:**
    (Esta es la sección principal. Identifica errores y áreas para mejorar. Para cada punto, explica el error brevemente y sugiere cómo corregirlo o mejorarlo. Si es posible, cita una pequeña parte del texto del estudiante y muestra la corrección.)

    *   **Contenido y Relevancia (Content & Relevance):**
        (¿Las ideas son relevantes para el tema? ¿Están bien desarrolladas? ¿Hay suficiente información o ejemplos?)
        - Ejemplo de comentario sobre contenido.

    *   **Logro Comunicativo y Tono (Communicative Achievement & Tone):**
        (¿La redacción cumple su propósito? ¿El tono es apropiado para la tarea y la audiencia prevista? ¿El mensaje es claro?)
        - Ejemplo de comentario sobre logro comunicativo.

    *   **Organización y Cohesión (Organisation & Cohesion):**
        (¿La redacción está bien estructurada? ¿Los párrafos están bien definidos y enlazados? ¿Se usan conectores de forma efectiva?)
        - Ejemplo de comentario sobre organización.

    *   **Gramática (Grammar):**
        (Errores en tiempos verbales, concordancia sujeto-verbo, artículos, preposiciones, estructura de la frase, etc. Para CADA error significativo, debes: 1. Citar la frase o parte del texto original. 2. Proporcionar la corrección directa. 3. Explicar brevemente la regla o la razón.)
        - Original: 'He go to school.' Corrección: 'He goes to school.' Explicación: El verbo necesita la '-es' en tercera persona del singular en presente simple.

    *   **Vocabulario (Vocabulary):**
        (Uso incorrecto de palabras, repetición, falta de variedad, colocaciones incorrectas, formalidad del vocabulario. Cita el original, da la corrección y una breve explicación.)
        - Ejemplo de comentario sobre vocabulario: "La palabra 'Z' podría reemplazarse por 'W' para mayor precisión."

    *   **Puntuación y Ortografía (Punctuation & Spelling):**
        (Errores de puntuación, mayúsculas, errores ortográficos. Cita el original, da la corrección y una breve explicación.)
        - Ejemplo de comentario sobre puntuación.

    **Sugerencias Adicionales:**
    (Cualquier otro consejo útil, como recursos para estudiar, áreas específicas en las que centrarse para la próxima vez, etc.)
    - Ejemplo de sugerencia adicional.

    **Nota Importante:** Sé específico en tus comentarios y proporciona ejemplos claros. El objetivo es educativo. Evita ser demasiado severo; enfócate en el aprendizaje.
    No reescribas la redacción completa. Solo proporciona ejemplos de corrección para ilustrar tus puntos.
    Utiliza Markdown para el formato del feedback (negritas, listas). Es crucial que sigas el formato Markdown exactamente como se describe.
    """

    messages = [
        SystemMessage(content=system_prompt_for_correction.strip()),
        HumanMessage(content=text_to_correct)
    ]
    
    provider_name = DEFAULT_LANGUAGE_MODEL_PROVIDER
    model_name = OPENAI_LANGUAGE_MODEL_NAME if provider_name == 'OPENAI' else GOOGLE_LANGUAGE_MODEL_NAME
    print(f"Enviando texto para corrección al LLM (Proveedor: {provider_name}, Modelo: {model_name})...")
    try:
        ai_response = await llm.ainvoke(messages)
        correction_feedback = str(ai_response.content) if ai_response.content else ""
        print("LLM Correction Response Content (first 500 chars):", correction_feedback[:500] + "...")
        return correction_feedback
    except Exception as e:
        print(f"Error al llamar al LLM de lenguaje para corrección: {e}")
        raise


# --- EJEMPLO DE PRUEBA (Puedes ejecutar este archivo directamente para probar) ---
if __name__ == "__main__":
    import asyncio
    
    test_student_text_example = """
    Hello teacher, my name is John. I want tell you about my holiday.
    Last summer, I goed to the beach with my family. It were very fun.
    The sun shined and the water are blue. We swimmed and play volleyball.
    I eated many ice cream. My brother, he falled down when play.
    I think vacations is very important for relax. I like so much my holiday.
    Thank for reading.
    """

    async def main_test():
        # --- Prueba de Transcripción ---
        # Necesitarás una URL de imagen real y accesible para que esto funcione.
        # test_image_url = "PON_AQUI_UNA_URL_DE_IMAGEN_MANUSCRITA_REAL" 
        # if test_image_url and test_image_url != "PON_AQUI_UNA_URL_DE_IMAGEN_MANUSCRITA_REAL":
        #     print(f"Probando TRANSCRIPCIÓN con el proveedor por defecto: {DEFAULT_VISION_MODEL_PROVIDER}")
        #     try:
        #         transcription = await transcribe_image_url_with_llm(test_image_url) 
        #         print("\n--- Transcripción Obtenida ---")
        #         print(transcription)
        #     except ValueError as ve:
        #         print(f"Error de configuración (Visión): {ve}")
        #     except Exception as e:
        #         print(f"Ocurrió un error durante la prueba de visión: {e}")
        # else:
        #     print("INFO: Prueba de transcripción omitida, no se proporcionó test_image_url.")

        print("\n" + "="*50 + "\n")

        # --- Prueba de Corrección ---
        print(f"Probando CORRECCIÓN con el proveedor por defecto: {DEFAULT_LANGUAGE_MODEL_PROVIDER}")
        try:
            feedback = await correct_text_with_llm(test_student_text_example)
            print("\n--- Feedback de Corrección Obtenido ---")
            print(feedback)
            print(f"\n(Versión del prompt de corrección usado: {CORRECTION_PROMPT_VERSION_CURRENT})")
        except ValueError as ve:
            print(f"Error de configuración (Lenguaje): {ve}")
        except Exception as e:
            print(f"Ocurrió un error durante la prueba de lenguaje: {e}")

    asyncio.run(main_test())
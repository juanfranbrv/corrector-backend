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
DEFAULT_LANGUAGE_MODEL_PROVIDER = os.getenv("DEFAULT_LANGUAGE_MODEL_PROVIDER", "OPENAI") # Default a OpenAI si no está
OPENAI_LANGUAGE_MODEL_NAME = os.getenv("OPENAI_LANGUAGE_MODEL_NAME", "gpt-4o") # Modelo potente para corrección
GOOGLE_LANGUAGE_MODEL_NAME = os.getenv("GOOGLE_LANGUAGE_MODEL_NAME", "gemini-1.5-pro-latest") # Modelo potente para corrección

# --- Versión del Prompt de Corrección ---
# Si cambias significativamente el prompt, puedes cambiar esta versión.
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
    
    elif DEFAULT_VISION_MODEL_PROVIDER == "GPT4O_MINI": # O "OPENAI"
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
                                      temperature=0.3, top_p=0.9) # Ajustar T y P para creatividad/facticidad
    
    elif DEFAULT_LANGUAGE_MODEL_PROVIDER == "OPENAI":
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY no está configurada para el modelo de lenguaje de OpenAI.")
        print(f"Usando modelo de lenguaje de OpenAI: {OPENAI_LANGUAGE_MODEL_NAME}")
        return ChatOpenAI(model=OPENAI_LANGUAGE_MODEL_NAME, openai_api_key=OPENAI_API_KEY,
                          temperature=0.3, top_p=0.9) # Ajustar T y P
    
    else:
        raise ValueError(f"Proveedor de modelo de lenguaje no soportado: {DEFAULT_LANGUAGE_MODEL_PROVIDER}")


async def transcribe_image_url_with_llm(image_url: str, prompt_text: str = "Transcribe el texto manuscrito en esta imagen.") -> str:
    """
    Toma una URL de imagen y un prompt, y usa el LLM de visión configurado
    para obtener una transcripción o descripción.
    """
    llm = get_vision_model_client()
    message_content = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    human_message = HumanMessage(content=message_content)
    
    print(f"Enviando imagen {image_url} y prompt de transcripción al LLM...")
    try:
        ai_response = await llm.ainvoke([human_message])
        transcription = str(ai_response.content) if ai_response.content else ""
        print("LLM Transcription Response Content:", transcription)
        return transcription
    except Exception as e:
        print(f"Error al llamar al LLM de visión para transcripción: {e}")
        raise


def get_default_correction_prompt_template() -> str:
    """
    Retorna la plantilla de prompt por defecto para la corrección de redacciones.
    """
    # Podrías añadir más personalización aquí, como el nivel del estudiante (ej. B1, B2)
    # si lo pasas como argumento y lo incluyes en el prompt.
    # Por ahora, un prompt general.
    prompt = """
    Eres un profesor de inglés experto, paciente y alentador, especializado en corregir redacciones de estudiantes.
    Tu objetivo es ayudar al estudiante a mejorar sus habilidades de escritura en inglés.

    Por favor, revisa la siguiente redacción escrita por un estudiante:
    ---
    {student_text}
    ---

    Tu tarea es proporcionar un feedback estructurado y constructivo. Sigue este formato:

    **Feedback General:**
    (Un breve resumen de tu impresión general sobre la redacción. Comienza con algo positivo si es posible.)

    **Puntos Fuertes:**
    (Enumera 2-3 aspectos positivos de la redacción. Ej: buen uso de vocabulario específico, ideas bien organizadas, argumento claro, etc.)
    - ...
    - ...

    **Áreas de Mejora:**
    (Esta es la sección principal. Identifica errores y áreas para mejorar. Para cada punto, explica el error brevemente y sugiere cómo corregirlo o mejorarlo. Si es posible, cita una pequeña parte del texto del estudiante y muestra la corrección.)

    *   **Contenido y Relevancia (Content & Relevance):**
        (¿Las ideas son relevantes para el tema? ¿Están bien desarrolladas? ¿Hay suficiente información o ejemplos?)
        - ...

    *   **Logro Comunicativo y Tono (Communicative Achievement & Tone):**
        (¿La redacción cumple su propósito? ¿El tono es apropiado para la tarea y la audiencia prevista? ¿El mensaje es claro?)
        - ...

    *   **Organización y Cohesión (Organisation & Cohesion):**
        (¿La redacción está bien estructurada? ¿Los párrafos están bien definidos y enlazados? ¿Se usan conectores de forma efectiva?)
        - ...

    *   **Gramática (Grammar):**
        (Errores en tiempos verbales, concordancia sujeto-verbo, artículos, preposiciones, estructura de la frase, etc.)
        - ...

    *   **Vocabulario (Vocabulary):**
        (Uso incorrecto de palabras, repetición, falta de variedad, colocaciones incorrectas, formalidad del vocabulario.)
        - ...

    *   **Puntuación y Ortografía (Punctuation & Spelling):**
        (Errores de puntuación, mayúsculas, errores ortográficos.)
        - ...

    **Sugerencias Adicionales:**
    (Cualquier otro consejo útil, como recursos para estudiar, áreas específicas en las que centrarse para la próxima vez, etc.)
    - ...

    **Nota Importante:** Sé específico en tus comentarios y proporciona ejemplos claros. El objetivo es educativo. Evita ser demasiado severo; enfócate en el aprendizaje.
    No reescribas la redacción completa. Solo proporciona ejemplos de corrección para ilustrar tus puntos.
    """
    return prompt.strip()


async def correct_text_with_llm(text_to_correct: str, student_level: str = "intermediate") -> str:
    """
    Toma un texto (la redacción transcrita) y usa el LLM de lenguaje configurado
    para obtener una corrección y feedback.
    'student_level' es opcional y podría usarse para adaptar el prompt en el futuro.
    """
    llm = get_language_model_client()

    # Obtener la plantilla de prompt
    # En el futuro, podríamos tener diferentes prompts para diferentes niveles o tipos de examen.
    system_prompt_content = get_default_correction_prompt_template()
    
    # Llenar la plantilla con el texto del estudiante.
    # Actualmente, el prompt no usa {student_level}, pero está como parámetro para futura expansión.
    # student_text_placeholder = "{student_text}"
    # if student_text_placeholder not in system_prompt_content:
    #     # Si el placeholder no está, simplemente añadimos el texto del estudiante después.
    #     # Esto es una salvaguarda, idealmente el prompt siempre tendrá el placeholder.
    #     # Mejor asegurarse que el prompt SIEMPRE tenga {student_text}
    #     final_prompt_text = system_prompt_content + f"\n\nRedacción del estudiante:\n---\n{text_to_correct}\n---"
    # else:
    #     final_prompt_text = system_prompt_content.format(student_text=text_to_correct)
    #
    # Langchain maneja mejor los roles, así que usaremos SystemMessage para el prompt principal
    # y HumanMessage para el texto del estudiante si el prompt está diseñado así.
    # En este caso, el prompt que diseñé incluye un placeholder {student_text}.

    system_message = SystemMessage(content="Sigues las instrucciones del usuario de manera precisa. Eres un profesor de inglés experto.") # Un rol general
    
    # El prompt detallado se pasa como parte del mensaje humano, después de una instrucción inicial.
    # O, mejor aún, ponemos el prompt largo como SystemMessage, y el texto del estudiante como HumanMessage.
    # Vamos a rehacer el prompt para que sea un SystemMessage y el HumanMessage sea el texto.

    # Revisión del prompt para LangChain:
    # El system_prompt_content ya está bien como system message.
    # Y el texto a corregir será el HumanMessage.
    # No, es mejor que el prompt principal que le dice al LLM qué hacer sea el SystemMessage
    # y el texto a procesar sea el HumanMessage.

    # Estructura correcta para muchos modelos:
    # SystemMessage: Define el rol y la tarea general del LLM.
    # HumanMessage: Contiene la solicitud específica del usuario, incluyendo los datos a procesar.

    # Vamos a refinar esto. El prompt `get_default_correction_prompt_template` ya incluye el placeholder `{student_text}`.
    # Langchain puede manejar esto bien.

    formatted_prompt = system_prompt_content.format(student_text=text_to_correct)
    
    # Podríamos usar un SystemMessage para el rol general y un HumanMessage con el prompt formateado.
    # messages = [
    #     SystemMessage(content="Eres un asistente de IA útil que sigue las instrucciones al pie de la letra."),
    #     HumanMessage(content=formatted_prompt)
    # ]
    # O, más comúnmente, el prompt *es* el HumanMessage si es una instrucción directa,
    # o el SystemMessage si define el comportamiento del LLM para la conversación.
    # Para tareas "one-shot" como esta, un HumanMessage con el prompt completo suele ser suficiente.
    # Pero para mayor control del "personaje" del LLM, SystemMessage + HumanMessage es mejor.

    messages = [
        # El SystemMessage define el "personaje" y la tarea general.
        SystemMessage(content=get_default_correction_prompt_template().replace("{student_text}", "")), # Le quitamos el placeholder aquí
        # El HumanMessage es el texto específico a procesar.
        HumanMessage(content=f"Aquí está la redacción del estudiante que necesito que corrijas:\n\n---\n{text_to_correct}\n---")
    ]
    # La idea anterior es una forma. Otra forma es usar el placeholder:
    # messages = [
    #   SystemMessage(content=get_default_correction_prompt_template().split("{student_text}")[0]), # Parte antes del placeholder
    #   HumanMessage(content=text_to_correct),
    #   SystemMessage(content=get_default_correction_prompt_template().split("{student_text}")[1]) # Parte después del placeholder
    # ]
    # Esto se vuelve complejo. La forma más simple y efectiva con Langchain es:
    # 1. SystemMessage: Describe el rol y la tarea general del LLM, pero SIN el texto del estudiante.
    # 2. HumanMessage: Contiene el texto del estudiante, precedido por una breve instrucción si es necesario.

    # Vamos a simplificar la estructura del prompt para que funcione bien con System + Human messages.
    # El SystemMessage será la instrucción general y el formato esperado.
    # El HumanMessage será el texto a corregir.

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
        (Errores en tiempos verbales, concordancia sujeto-verbo, artículos, preposiciones, estructura de la frase, etc.)
        - Ejemplo de comentario sobre gramática: "En la frase 'X', sería mejor decir 'Y' porque..."

    *   **Vocabulario (Vocabulary):**
        (Uso incorrecto de palabras, repetición, falta de variedad, colocaciones incorrectas, formalidad del vocabulario.)
        - Ejemplo de comentario sobre vocabulario: "La palabra 'Z' podría reemplazarse por 'W' para mayor precisión."

    *   **Puntuación y Ortografía (Punctuation & Spelling):**
        (Errores de puntuación, mayúsculas, errores ortográficos.)
        - Ejemplo de comentario sobre puntuación.

    **Sugerencias Adicionales:**
    (Cualquier otro consejo útil, como recursos para estudiar, áreas específicas en las que centrarse para la próxima vez, etc.)
    - Ejemplo de sugerencia adicional.

    **Nota Importante:** Sé específico en tus comentarios y proporciona ejemplos claros. El objetivo es educativo. Evita ser demasiado severo; enfócate en el aprendizaje.
    No reescribas la redacción completa. Solo proporciona ejemplos de corrección para ilustrar tus puntos.
    Utiliza Markdown para el formato del feedback (negritas, listas).
    """

    messages = [
        SystemMessage(content=system_prompt_for_correction.strip()),
        HumanMessage(content=text_to_correct)
    ]
    
    print(f"Enviando texto para corrección al LLM (Proveedor: {DEFAULT_LANGUAGE_MODEL_PROVIDER}, Modelo: {OPENAI_LANGUAGE_MODEL_NAME if DEFAULT_LANGUAGE_MODEL_PROVIDER == 'OPENAI' else GOOGLE_LANGUAGE_MODEL_NAME})...")
    try:
        ai_response = await llm.ainvoke(messages) # Usar ainvoke para llamadas asíncronas
        
        correction_feedback = str(ai_response.content) if ai_response.content else ""
        print("LLM Correction Response Content:", correction_feedback[:500] + "...") # Imprimir solo una parte
        return correction_feedback
    except Exception as e:
        print(f"Error al llamar al LLM de lenguaje para corrección: {e}")
        # Podrías querer manejar diferentes tipos de errores aquí (API, red, etc.)
        raise  # Re-lanzar la excepción para que el endpoint la maneje


# --- EJEMPLO DE PRUEBA (Puedes ejecutar este archivo directamente para probar) ---
if __name__ == "__main__":
    import asyncio
    
    # Prueba de transcripción (requiere una imagen)
    # test_image_url = "https://images.unsplash.com/photo-1579705379005-1cdcdc76f793?q=80&w=1287&auto=format&fit=crop&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D"
    
    # Prueba de corrección (requiere texto)
    test_student_text = """
    Hello teacher, my name is John. I want tell you about my holiday.
    Last summer, I goed to the beach with my family. It were very fun.
    The sun shined and the water are blue. We swimmed and play volleyball.
    I eated many ice cream. My brother, he falled down when play.
    I think vacations is very important for relax. I like so much my holiday.
    Thank for reading.
    """

    async def main_test():
        # --- Prueba de Transcripción (comentada por defecto si no tienes imagen a mano) ---
        # print(f"Probando TRANSCRIPCIÓN con el proveedor por defecto: {DEFAULT_VISION_MODEL_PROVIDER}")
        # try:
        #     transcription = await transcribe_image_url_with_llm(test_image_url, "Describe brevemente esta imagen.")
        #     print("\n--- Transcripción/Descripción Obtenida ---")
        #     print(transcription)
        # except ValueError as ve:
        #     print(f"Error de configuración (Visión): {ve}")
        # except Exception as e:
        #     print(f"Ocurrió un error durante la prueba de visión: {e}")

        print("\n" + "="*50 + "\n")

        # --- Prueba de Corrección ---
        print(f"Probando CORRECCIÓN con el proveedor por defecto: {DEFAULT_LANGUAGE_MODEL_PROVIDER}")
        try:
            # Puedes cambiar DEFAULT_LANGUAGE_MODEL_PROVIDER en .env o aquí directamente para probar
            # os.environ["DEFAULT_LANGUAGE_MODEL_PROVIDER"] = "GOOGLE" # o "OPENAI"
            
            # Reinicializar si cambiaste la variable de entorno programáticamente
            # global DEFAULT_LANGUAGE_MODEL_PROVIDER_FROM_ENV 
            # DEFAULT_LANGUAGE_MODEL_PROVIDER_FROM_ENV = os.getenv("DEFAULT_LANGUAGE_MODEL_PROVIDER", "OPENAI")

            feedback = await correct_text_with_llm(test_student_text)
            print("\n--- Feedback de Corrección Obtenido ---")
            print(feedback)
            print(f"\n(Versión del prompt usado: {CORRECTION_PROMPT_VERSION_CURRENT})")
        except ValueError as ve:
            print(f"Error de configuración (Lenguaje): {ve}")
        except Exception as e:
            print(f"Ocurrió un error durante la prueba de lenguaje: {e}")

    asyncio.run(main_test())
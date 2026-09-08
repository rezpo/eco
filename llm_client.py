"""
LLM Client - Abstracción para múltiples proveedores de LLM
Soporta: Anthropic, OpenAI, MiMo, MiniMax
"""

import os
from abc import ABC, abstractmethod
from typing import Optional


class LLMClient(ABC):
    """Interfaz base para clientes LLM."""

    @abstractmethod
    def chat(self, message: str, context: str = "", system_prompt: str = "", images: list = None) -> str:
        """Envía un mensaje y retorna la respuesta."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Verifica si el cliente está configurado correctamente."""
        pass


class AnthropicClient(LLMClient):
    """Cliente para Claude de Anthropic."""

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(self.api_key)

    def chat(self, message: str, context: str = "", system_prompt: str = "", images: list = None) -> str:
        client = self._get_client()

        full_context = ""
        if context:
            full_context = f"\n\n--- Contexto de la reunión (últimos minutos) ---\n{context}\n--- Fin del contexto ---\n"

        import base64
        import os

        user_content = []
        user_content.append({
            "type": "text",
            "text": f"{full_context}\n\n{message}"
        })

        if images:
            for img_path in images:
                if os.path.exists(img_path):
                    try:
                        with open(img_path, "rb") as f:
                            img_data = base64.b64encode(f.read()).decode("utf-8")
                        user_content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_data
                            }
                        })
                    except Exception:
                        pass

        messages = [
            {
                "role": "user",
                "content": user_content
            }
        ]

        response = client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt or "Eres un asistente inteligente que ayuda durante reuniones. Analiza la conversación y responde preguntas de manera concisa y útil. Responde en el mismo idioma que la conversación.",
            messages=messages
        )

        return response.content[0].text


class OpenAIClient(LLMClient):
    """Cliente para GPT de OpenAI."""

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4")
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(api_key=self.api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(self.api_key)

    def chat(self, message: str, context: str = "", system_prompt: str = "", images: list = None) -> str:
        client = self._get_client()

        full_context = ""
        if context:
            full_context = f"\n\n--- Contexto de la reunión (últimos minutos) ---\n{context}\n--- Fin del contexto ---\n"

        import base64
        import os

        user_content = []
        user_content.append({
            "type": "text",
            "text": f"{full_context}\n\n{message}"
        })

        if images:
            for img_path in images:
                if os.path.exists(img_path):
                    try:
                        with open(img_path, "rb") as f:
                            img_data = base64.b64encode(f.read()).decode("utf-8")
                        user_content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_data}"
                            }
                        })
                    except Exception:
                        pass

        messages = [
            {
                "role": "system",
                "content": system_prompt or "Eres un asistente inteligente que ayuda durante reuniones. Analiza la conversación y responde preguntas de manera concisa y útil. Responde en el mismo idioma que la conversación."
            },
            {
                "role": "user",
                "content": user_content
            }
        ]

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=1024
        )

        return response.choices[0].message.content


class MiMoClient(LLMClient):
    """Cliente para MiMo (API compatible con OpenAI)."""

    def __init__(self):
        self.api_key = os.getenv("MIMO_API_KEY", "")
        self.base_url = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
        self.model = os.getenv("MIMO_MODEL", "mimo-v2.5-pro")
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
        return self._client

    def is_available(self) -> bool:
        return bool(self.api_key)

    def chat(self, message: str, context: str = "", system_prompt: str = "", images: list = None) -> str:
        client = self._get_client()

        full_context = ""
        if context:
            full_context = f"\n\n--- Contexto de la reunión (últimos minutos) ---\n{context}\n--- Fin del contexto ---\n"

        import base64
        import os

        user_content = []
        user_content.append({
            "type": "text",
            "text": f"{full_context}\n\n{message}"
        })

        if images:
            for img_path in images:
                if os.path.exists(img_path):
                    try:
                        with open(img_path, "rb") as f:
                            img_data = base64.b64encode(f.read()).decode("utf-8")
                        user_content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_data}"
                            }
                        })
                    except Exception:
                        pass

        messages = [
            {
                "role": "system",
                "content": system_prompt or "Eres un asistente inteligente que ayuda durante reuniones. Analiza la conversación y responde preguntas de manera concisa y útil. Responde en el mismo idioma que la conversación."
            },
            {
                "role": "user",
                "content": user_content
            }
        ]

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=1024
        )

        return response.choices[0].message.content


class MiniMaxClient(LLMClient):
    """Cliente para MiniMax."""

    def __init__(self):
        self.api_key = os.getenv("MINIMAX_API_KEY", "")
        self.base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
        self.model = os.getenv("MINIMAX_MODEL", "abab6.5-chat")
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
        return self._client

    def is_available(self) -> bool:
        return bool(self.api_key)

    def chat(self, message: str, context: str = "", system_prompt: str = "", images: list = None) -> str:
        client = self._get_client()

        full_context = ""
        if context:
            full_context = f"\n\n--- Contexto de la reunión (últimos minutos) ---\n{context}\n--- Fin del contexto ---\n"

        import base64
        import os

        user_content = []
        user_content.append({
            "type": "text",
            "text": f"{full_context}\n\n{message}"
        })

        if images:
            for img_path in images:
                if os.path.exists(img_path):
                    try:
                        with open(img_path, "rb") as f:
                            img_data = base64.b64encode(f.read()).decode("utf-8")
                        user_content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_data}"
                            }
                        })
                    except Exception:
                        pass

        messages = [
            {
                "role": "system",
                "content": system_prompt or "Eres un asistente inteligente que ayuda durante reuniones. Analiza la conversación y responde preguntas de manera concisa y útil. Responde en el mismo idioma que la conversación."
            },
            {
                "role": "user",
                "content": user_content
            }
        ]

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=1024
        )

        return response.choices[0].message.content


# Registro de proveedores
PROVIDERS = {
    "anthropic": AnthropicClient,
    "openai": OpenAIClient,
    "mimo": MiMoClient,
    "minimax": MiniMaxClient,
}


def get_llm_client(provider: str = None) -> LLMClient:
    """
    Obtiene un cliente LLM según el proveedor especificado.
    Si no se especifica, usa el primero disponible.
    """
    if provider:
        provider = provider.lower()
        if provider not in PROVIDERS:
            raise ValueError(f"Proveedor '{provider}' no soportado. Opciones: {list(PROVIDERS.keys())}")
        client = PROVIDERS[provider]()
        if not client.is_available():
            raise ValueError(f"Proveedor '{provider}' no tiene API key configurada.")
        return client

    # Buscar el primero disponible
    for name, client_class in PROVIDERS.items():
        client = client_class()
        if client.is_available():
            return client

    raise ValueError(
        "No hay ningún proveedor LLM configurado. "
        "Configura una API key en las variables de entorno: "
        "ANTHROPIC_API_KEY, OPENAI_API_KEY, MIMO_API_KEY, o MINIMAX_API_KEY"
    )


def list_available_providers() -> list:
    """Lista los proveedores disponibles."""
    available = []
    for name, client_class in PROVIDERS.items():
        client = client_class()
        if client.is_available():
            available.append(name)
    return available

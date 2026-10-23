"""
LLM Client - Abstraction layer for multiple LLM providers.
Supports: Anthropic, OpenAI, MiMo, MiniMax.
"""

import os
from abc import ABC, abstractmethod
from typing import Optional


class LLMClient(ABC):
    """Base interface for LLM clients."""

    @abstractmethod
    def chat(self, message: str, context: str = "", system_prompt: str = "", images: list = None) -> str:
        """Send a message and return the response."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Verify whether the client is configured with valid credentials."""
        pass


class AnthropicClient(LLMClient):
    """Client for Anthropic Claude."""

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
            full_context = f"\n\n--- Meeting context (recent minutes) ---\n{context}\n--- End of context ---\n"

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
            system=system_prompt or "You are an intelligent assistant helping during meetings. Analyze the conversation and answer questions concisely and usefully. Respond in the same language as the conversation.",
            messages=messages
        )

        return response.content[0].text


class OpenAIClient(LLMClient):
    """Client for OpenAI GPT."""

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
            full_context = f"\n\n--- Meeting context (recent minutes) ---\n{context}\n--- End of context ---\n"

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
                "content": system_prompt or "You are an intelligent assistant helping during meetings. Analyze the conversation and answer questions concisely and usefully. Respond in the same language as the conversation."
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
    """Client for MiMo (OpenAI-compatible API)."""

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
            full_context = f"\n\n--- Meeting context (recent minutes) ---\n{context}\n--- End of context ---\n"

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
                "content": system_prompt or "You are an intelligent assistant helping during meetings. Analyze the conversation and answer questions concisely and usefully. Respond in the same language as the conversation."
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
    """Client for MiniMax."""

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
            full_context = f"\n\n--- Meeting context (recent minutes) ---\n{context}\n--- End of context ---\n"

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
                "content": system_prompt or "You are an intelligent assistant helping during meetings. Analyze the conversation and answer questions concisely and usefully. Respond in the same language as the conversation."
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


# Provider registry
PROVIDERS = {
    "anthropic": AnthropicClient,
    "openai": OpenAIClient,
    "mimo": MiMoClient,
    "minimax": MiniMaxClient,
}


def get_llm_client(provider: str = None) -> LLMClient:
    """
    Get an LLM client for the specified provider.
    If none is specified, returns the first available provider.
    """
    if provider:
        provider = provider.lower()
        if provider not in PROVIDERS:
            raise ValueError(f"Provider '{provider}' not supported. Options: {list(PROVIDERS.keys())}")
        client = PROVIDERS[provider]()
        if not client.is_available():
            raise ValueError(f"Provider '{provider}' has no API key configured.")
        return client

    # Search for first available provider
    for name, client_class in PROVIDERS.items():
        client = client_class()
        if client.is_available():
            return client

    raise ValueError(
        "No LLM provider configured. "
        "Set an API key in your environment variables or ~/.eco.env: "
        "ANTHROPIC_API_KEY, OPENAI_API_KEY, MIMO_API_KEY, or MINIMAX_API_KEY"
    )


def list_available_providers() -> list:
    """List available LLM providers."""
    available = []
    for name, client_class in PROVIDERS.items():
        client = client_class()
        if client.is_available():
            available.append(name)
    return available

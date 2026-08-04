#!/usr/bin/env python3
"""
Script veloce per testare se una API key OpenAI funziona.
Uso:
    export OPENAI_API_KEY="sk-..."
    python test_openai_key.py

Oppure passala come argomento:
    python test_openai_key.py sk-...
"""

import os
import sys


def test_openai_key(api_key: str) -> None:
    try:
        from openai import OpenAI
    except ImportError:
        print("❌ Libreria 'openai' non installata. Esegui: pip install openai")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    print("🔍 Test 1: Recupero lista modelli disponibili...")
    try:
        models = client.models.list()
        print(f"✅ Key valida! Modelli disponibili: {len(models.data)}")
    except Exception as e:
        print(f"❌ Errore nel recupero dei modelli: {e}")
        sys.exit(1)

    print("\n🔍 Test 2: Chiamata di prova a chat.completions...")
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Rispondi solo con: OK"}],
            max_tokens=5,
        )
        content = response.choices[0].message.content.strip()
        print(f"✅ Risposta ricevuta: '{content}'")
    except Exception as e:
        print(f"⚠️  Errore nella chiamata chat.completions: {e}")
        sys.exit(1)

    print("\n🎉 La API key funziona correttamente!")


if __name__ == "__main__":
    key = None

    if len(sys.argv) > 1:
        key = sys.argv[1]
    else:
        key = os.environ.get("OPENAI_API_KEY")

    if not key:
        print("❌ Nessuna API key trovata.")
        print(
            "Passa la key come argomento oppure imposta la variabile d'ambiente OPENAI_API_KEY."
        )
        sys.exit(1)

    test_openai_key(key)

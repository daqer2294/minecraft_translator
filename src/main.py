# src/main.py
from __future__ import annotations
import argparse
import os
import json

from .translators import Translator
from .utils.cache import TranslationCache
from . import config
from .scanner import build_resource_pack


def main():
    parser = argparse.ArgumentParser(
        description="Minecraft Translator — AI русификатор модов."
    )
    parser.add_argument(
        "--input",
        help="Путь к клиенту Minecraft (.minecraft) или папке с частичной сборкой",
        required=False,
    )
    parser.add_argument(
        "--out",
        default="./out_pack",
        help="Папка, куда будет собран ресурс-пак",
    )
    parser.add_argument(
        "--model",
        default=config.TRANSLATOR_MODEL,
        help="Модель OpenAI (по умолчанию gpt-4o-mini)",
    )
    parser.add_argument(
        "--provider",
        default=config.TRANSLATOR_PROVIDER,
        help="Провайдер перевода (openai/ollama/dry)",
    )
    parser.add_argument(
        "--cache",
        default=config.DEFAULT_CACHE_PATH,
        help="Путь к JSON-файлу кэша переводов",
    )
    parser.add_argument(
        "--set-key",
        help="Сохранить OpenAI API ключ в secrets.json и выйти (пример: --set-key sk-XXXX)",
    )

    args = parser.parse_args()

    # --- режим записи ключа и выход ---
    if args.set_key:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        secrets_path = os.path.join(project_root, "secrets.json")
        os.makedirs(project_root, exist_ok=True)
        with open(secrets_path, "w", encoding="utf-8") as f:
            json.dump({"OPENAI_API_KEY": args.set_key}, f, ensure_ascii=False, indent=2)
        print("🔑 Ключ сохранён в", secrets_path)
        return

    base_input = args.input or input("Укажи путь к папке клиента (.minecraft/или твоя папка сборки): ").strip()

    # --- инициализация ---
    print("🚀 Инициализация переводчика...")
    cache = TranslationCache(args.cache)
    translator = Translator(
        provider=args.provider,
        model=args.model,
        api_key=config.OPENAI_API_KEY,
        cache=cache,
    )

    # --- сборка ресурс-пака ---
    build_resource_pack(
        base_input=base_input,
        out_root=args.out,
        translator=translator,
    )

    print("\n✅ Всё готово! Ресурс-пак создан:")
    print(os.path.abspath(args.out))


if __name__ == "__main__":
    main()

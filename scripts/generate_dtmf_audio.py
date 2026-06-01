"""
Generate DTMF language-selection audio prompts using Azure Neural TTS.

Usage (run from project root):
    python scripts/generate_dtmf_audio.py

Output files written to tts_cache/:
    dtmf_prompt_ur.wav   — Urdu prompt (ur-PK-UzmaNeural)
    dtmf_prompt_en.wav   — English prompt (en-US-JennyNeural)
    dtmf_prompt_combined.wav — Bilingual combined prompt

Requires AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in .env.
"""
from __future__ import annotations

import os
import sys

# Allow running from project root without install
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

AZURE_KEY = os.environ.get("AZURE_SPEECH_KEY", "")
AZURE_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus")
OUTPUT_DIR = os.environ.get("TTS_CACHE_DIR", "tts_cache")

PROMPTS = {
    "dtmf_prompt_ur": {
        "voice": "ur-PK-UzmaNeural",
        "language": "ur-PK",
        "text": (
            "السلام علیکم! آپ کا شکریہ کہ آپ نے ہمیں کال کی۔ "
            "اردو کے لیے ایک دبائیں۔ "
            "پنجابی کے لیے دو دبائیں۔ "
            "انگریزی کے لیے تین دبائیں۔"
        ),
    },
    "dtmf_prompt_en": {
        "voice": "en-US-JennyNeural",
        "language": "en-US",
        "text": (
            "Thank you for calling. "
            "Press 1 for Urdu. "
            "Press 2 for Punjabi. "
            "Press 3 for English."
        ),
    },
    "dtmf_prompt_combined": {
        "voice": "ur-PK-UzmaNeural",
        "language": "ur-PK",
        "text": (
            "السلام علیکم! اردو کے لیے ایک دبائیں۔ "
            "پنجابی کے لیے دو دبائیں۔ "
            "انگریزی کے لیے تین دبائیں۔ "
            "Press 1 for Urdu, 2 for Punjabi, 3 for English."
        ),
    },
}


def generate_azure_wav(voice: str, language: str, text: str, output_path: str) -> None:
    import azure.cognitiveservices.speech as speechsdk

    speech_config = speechsdk.SpeechConfig(
        subscription=AZURE_KEY,
        region=AZURE_REGION,
    )
    speech_config.speech_synthesis_voice_name = voice
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
    )

    audio_config = speechsdk.audio.AudioOutputConfig(filename=output_path)
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    result = synthesizer.speak_text_async(text).get()
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        print(f"  ✓ {output_path}")
    else:
        cancellation = result.cancellation_details
        raise RuntimeError(
            f"Azure TTS failed for {output_path}: {cancellation.reason} — {cancellation.error_details}"
        )


def main() -> None:
    if not AZURE_KEY:
        print("ERROR: AZURE_SPEECH_KEY not set in .env — cannot generate audio files.")
        print("Set the key and re-run: python scripts/generate_dtmf_audio.py")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Generating DTMF audio files → {OUTPUT_DIR}/")

    for filename, config in PROMPTS.items():
        output_path = os.path.join(OUTPUT_DIR, f"{filename}.wav")
        print(f"  Generating {filename}.wav ({config['voice']}) ...")
        generate_azure_wav(
            voice=config["voice"],
            language=config["language"],
            text=config["text"],
            output_path=output_path,
        )

    print("\nDone. Files generated:")
    for filename in PROMPTS:
        path = os.path.join(OUTPUT_DIR, f"{filename}.wav")
        size_kb = os.path.getsize(path) / 1024 if os.path.exists(path) else 0
        print(f"  {path}  ({size_kb:.1f} KB)")

    print(
        "\nNext step: configure your Plivo answer URL to serve dtmf_prompt_combined.wav "
        "as the initial DTMF prompt, then route based on DTMF digit to the voice pipeline."
    )


if __name__ == "__main__":
    main()

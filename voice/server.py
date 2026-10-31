"""Voice in and out for RAGstofan, all open source and free, on the Spark's GPU.

Speech to text: Whisper large-v3-turbo (MIT), English and Icelandic and ~100 more, about 0.8 s a question.
Text to speech: Piper (GPL-3.0) with Joe for English (CC0) and Steinn or Ugla for Icelandic (Talrómur, CC BY 4.0).

Runs apart from the main server, in its own environment (.venv-voice, CUDA PyTorch), so either can restart alone:
    cd ~/RAGstofan && HF_HOME=.voice .venv-voice/bin/uvicorn voice.server:app --host 0.0.0.0 --port 8001
"""

import io
import os
import re
import tempfile
import wave
from pathlib import Path

import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from faster_whisper.audio import decode_audio
from piper import PiperVoice
from pydantic import BaseModel
from transformers import pipeline

VOICES = Path(os.getenv("VOICE_DIR", Path(__file__).resolve().parent.parent / ".voice"))
STT_MODEL = os.getenv("STT_MODEL", "openai/whisper-large-v3-turbo")
SPEAKERS = {"en": os.getenv("TTS_EN", "en_US-joe-medium"), "is": os.getenv("TTS_IS", "is_IS-steinn-medium")}

app = FastAPI(title="RAGstofan voice")
asr = pipeline("automatic-speech-recognition", model=STT_MODEL, torch_dtype=torch.float16,
               device="cuda:0" if torch.cuda.is_available() else "cpu")
speakers = {lang: PiperVoice.load(str(VOICES / f"{name}.onnx")) for lang, name in SPEAKERS.items()}
WHISPER_LANG = {"en": "english", "is": "icelandic"}


@app.get("/health")
def health():
    return {"stt": STT_MODEL, "tts": SPEAKERS, "device": str(asr.device)}


@app.post("/stt")
async def stt(audio: UploadFile = File(...), language: str | None = Form(None)):
    """Recorded speech (m4a, wav, webm...) to text. language "en" or "is" fixes it; otherwise Whisper detects it."""
    data = await audio.read()
    if not data:
        raise HTTPException(400, "empty recording")
    with tempfile.NamedTemporaryFile(suffix=Path(audio.filename or "a.m4a").suffix) as f:
        f.write(data)
        f.flush()
        samples = decode_audio(f.name, sampling_rate=16000)
    kwargs = {"task": "transcribe"}
    if language in WHISPER_LANG:
        kwargs["language"] = WHISPER_LANG[language]
    out = asr({"raw": samples, "sampling_rate": 16000}, return_language=True, generate_kwargs=kwargs)
    lang = (out.get("chunks") or [{}])[0].get("language") or WHISPER_LANG.get(language or "", "")
    return {"text": out["text"].strip(), "language": "is" if lang == "icelandic" else "en" if lang == "english" else lang}


class Speak(BaseModel):
    text: str
    language: str = "en"


_MONTHS = {"en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                  "November", "December"],
           "is": ["janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí", "ágúst", "september", "október",
                  "nóvember", "desember"]}


def readable(text: str, lang: str) -> str:
    """Table codes read aloud badly: "2026M09" becomes "September 2026", "2026Q2" "second quarter of 2026"."""
    months = _MONTHS["is" if lang == "is" else "en"]
    text = re.sub(r"\b(\d{4})M(\d{2})\b", lambda m: f"{months[int(m[2]) - 1]} {m[1]}", text)
    text = re.sub(r"\b(\d{4})Q(\d)\b",
                  lambda m: f"{m[2]}. ársfjórðungi {m[1]}" if lang == "is" else f"quarter {m[2]} of {m[1]}", text)
    return text


@app.post("/tts")
def tts(req: Speak):
    """Text to a WAV file in the answer's language."""
    voice = speakers["is" if req.language == "is" else "en"]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        voice.synthesize_wav(readable(req.text, req.language), w)
    return Response(buf.getvalue(), media_type="audio/wav")

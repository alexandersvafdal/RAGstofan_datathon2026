"""Voice in and out for RAGstofan, all open source and free, on the Spark's GPU.

Speech to text: Whisper large-v3-turbo (MIT), English and Icelandic and ~100 more, about 0.8 s a question.
Text to speech: English by Kokoro-82M (Apache-2.0), Icelandic by Piper (GPL-3.0) with Steinn or Ugla (Talrómur,
CC BY 4.0); Piper's Joe (CC0) stands in for English if Kokoro is missing.

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
import numpy as np
import soundfile as sf
from piper import PiperVoice, SynthesisConfig
from pydantic import BaseModel
from transformers import pipeline

VOICES = Path(os.getenv("VOICE_DIR", Path(__file__).resolve().parent.parent / ".voice"))
STT_MODEL = os.getenv("STT_MODEL", "openai/whisper-large-v3-turbo")
SPEAKERS = {"en": os.getenv("TTS_EN", "en_US-joe-medium"), "is": os.getenv("TTS_IS", "is_IS-steinn-medium")}
KOKORO_VOICE = os.getenv("TTS_EN_KOKORO", "af_heart")   # "" turns Kokoro off
PACE = float(os.getenv("TTS_PACE", "0.85"))              # Piper's length scale: below 1 speaks faster

app = FastAPI(title="RAGstofan voice")
asr = pipeline("automatic-speech-recognition", model=STT_MODEL, torch_dtype=torch.float16,
               device="cuda:0" if torch.cuda.is_available() else "cpu")
# Every voice a listener can pick, per language. Kokoro voices are named like "af_heart"; Piper's are .onnx files.
CHOICES = {
    "en": [("af_heart", "Heart", "Kokoro"), ("am_michael", "Michael", "Kokoro"), ("bf_emma", "Emma · British", "Kokoro"),
           ("bm_george", "George · British", "Kokoro"), ("en_US-joe-medium", "Joe", "Piper")],
    "is": [("is_IS-steinn-medium", "Steinn", "Piper"), ("is_IS-ugla-medium", "Ugla", "Piper")],
}
piper_voices = {name: PiperVoice.load(str(VOICES / f"{name}.onnx"))
                for name in {n for opts in CHOICES.values() for n, _, e in opts if e == "Piper"} | set(SPEAKERS.values())
                if (VOICES / f"{name}.onnx").exists()}
speakers = {lang: piper_voices[name] for lang, name in SPEAKERS.items()}
try:
    from kokoro import KPipeline
    kokoro = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M") if KOKORO_VOICE else None
    # British voices ("bf_", "bm_") pronounce through their own pipeline, sharing the American one's model
    kokoro_gb = KPipeline(lang_code="b", repo_id="hexgrad/Kokoro-82M", model=kokoro.model) if kokoro else None
except Exception:   # not installed or no weights: Piper reads English too
    kokoro = None
WHISPER_LANG = {"en": "english", "is": "icelandic"}


@app.get("/health")
def health():
    return {"stt": STT_MODEL, "tts": {**SPEAKERS, **({"en": f"kokoro {KOKORO_VOICE}"} if kokoro else {})},
            "device": str(asr.device)}


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


@app.get("/voices")
def voices():
    """The voices to choose from, with the default for each language."""
    available = {lang: [{"id": i, "name": n, "engine": e} for i, n, e in opts
                        if (e == "Kokoro" and kokoro) or i in piper_voices] for lang, opts in CHOICES.items()}
    return {"voices": available,
            "default": {"en": KOKORO_VOICE if kokoro else SPEAKERS["en"], "is": SPEAKERS["is"]}}


class Speak(BaseModel):
    text: str
    language: str = "en"
    voice: str | None = None   # one of /voices; None: the language's default


_MONTHS = {"en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                  "November", "December"],
           "is": ["janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí", "ágúst", "september", "október",
                  "nóvember", "desember"]}


def readable(text: str, lang: str) -> str:
    """What a listener needs, said the way a person would: "2026M09" becomes "September 2026", "6%" "6 percent",
    and table codes and the closing source line, which belong on screen, are left out."""
    months = _MONTHS["is" if lang == "is" else "en"]
    text = re.sub(r"\s*\((?:tafla|töflu|table)\s+[A-Z]{3}\d+\w*\)", "", text, flags=re.I)
    text = re.sub(r",?\s*(?:tafla|töflu|table)\s+[A-Z]{3}\d+\w*,?", "", text, flags=re.I)
    text = re.sub(r"\s*(?:Source|Heimild):[^.]*\.?", "", text)
    text = re.sub(r"\s*\((?:Hagstofa Íslands|Statistics Iceland)\)", "", text)
    text = re.sub(r"\s*%", " prósent" if lang == "is" else " percent", text)
    text = re.sub(r"\b(\d{4})M(\d{2})\b", lambda m: f"{months[int(m[2]) - 1]} {m[1]}", text)
    text = re.sub(r"\b(\d{4})Q(\d)\b",
                  lambda m: f"{m[2]}. ársfjórðungi {m[1]}" if lang == "is" else f"quarter {m[2]} of {m[1]}", text)
    return text


@app.post("/tts")
def tts(req: Speak):
    """Text to a WAV file in the answer's language."""
    text = readable(req.text, req.language)
    lang = "is" if req.language == "is" else "en"
    voice = req.voice
    if voice not in piper_voices and not (kokoro and voice and re.fullmatch(r"[ab][fm]_\w+", voice) and lang == "en"):
        voice = KOKORO_VOICE if lang == "en" and kokoro else SPEAKERS[lang]
    buf = io.BytesIO()
    if voice in piper_voices:
        with wave.open(buf, "wb") as w:
            piper_voices[voice].synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=PACE))
    else:
        pipe = kokoro_gb if voice.startswith("b") else kokoro
        audio = np.concatenate([a.numpy() for _, _, a in pipe(text, voice=voice, speed=1.05)])
        sf.write(buf, audio, 24000, format="WAV", subtype="PCM_16")
    return Response(buf.getvalue(), media_type="audio/wav")

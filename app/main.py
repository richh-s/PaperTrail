from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google import genai
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

from app.config import settings

# Lazy-init tracing: only wire up Langfuse if keys are provided.
# Missing keys at startup won't crash the service.
_langfuse: Langfuse | None = None
if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
    _langfuse = Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_HOST,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Drain the Langfuse background queue before the process exits so
    # no traces are silently lost during scale-down or container shutdown.
    if _langfuse:
        _langfuse.flush()


app = FastAPI(title="Agentic AI Core Service", lifespan=lifespan)

gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)


class GenerateRequest(BaseModel):
    prompt: str
    model: str = "gemini-2.0-flash"


class GenerateResponse(BaseModel):
    text: str
    model: str


@observe()  # Every call creates a Langfuse trace capturing input, output, and latency
def _call_gemini(prompt: str, model: str) -> str:
    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"model": model},
    )
    response = gemini_client.models.generate_content(
        model=model,
        contents=prompt,
    )
    text = response.text
    langfuse_context.update_current_observation(output=text)
    return text


@app.get("/healthz")
async def health_check():
    return {"status": "healthy", "service": "agent_core"}


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    try:
        text = _call_gemini(prompt=request.prompt, model=request.model)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return GenerateResponse(text=text, model=request.model)
